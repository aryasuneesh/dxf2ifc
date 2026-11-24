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


STYLE = """<style>:root{color-scheme:light} body{font:17px/1.6 Segoe UI,Arial,sans-serif;color:#1c1c1c;background:#fff;
max-width:780px;margin:40px auto;padding:0 20px} a{color:#1d5fbf} li{margin:8px 0} span{color:#555;font-size:15px}
h2{margin-top:28px} .box{background:#eef4fc;border-left:5px solid #1d5fbf;padding:10px 16px}
table{border-collapse:collapse;width:100%} td{border-bottom:1px solid #ddd;padding:6px 8px;vertical-align:top}
td:first-child{white-space:nowrap;font-weight:600}</style>"""


def offline_page(names) -> bytes:
    """START HERE page of the zip sent by email: what to do, and every answer we need from the engineer."""
    e = html.escape
    core = 2  # the first plans matter most; the rest are optional extras
    plans = "".join(f"<li><a href='{e(n)}/viewer.html'><b>{e(n)}</b></a>{' (optional)' if i >= core else ''}</li>"
                    for i, n in enumerate(names)) or "<li>No plans yet.</li>"
    time = (f"about 10 minutes per plan. Please do the first {core}; the other {len(names) - core} are optional, "
            "if you have time." if len(names) > core else f"about 10 minutes per plan, {len(names)} plan{'s' if len(names) != 1 else ''}.")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>DXF to IFC: user test</title>{STYLE}</head><body>
<h1>From a 2D plan to a model you can check</h1>
<p>A program turned each floor plan below (a DXF drawing) into a 3D building model (IFC). Where the drawing did not say
enough, the program made a guess. We would like you, as an engineer, to tell us whether those guesses are right.</p>
<p><b>Time:</b> {time}</p>

<div class="box"><b>Before you start</b><ol>
<li><b>Unzip the whole folder</b> (right-click the zip, Extract all). Files opened from inside the zip do not work.</li>
<li>Open the plans in <b>Chrome or Edge</b>. Other browsers work too, but cannot save your answers to a file by themselves.</li>
<li>No internet needed. Nothing is sent anywhere: your answers stay on your computer until you email them.</li></ol></div>

<h2>Plans</h2><ol>{plans}</ol>

<h2>What you will see</h2><ul>
<li>The 3D model in the middle. Drag to turn it, scroll to zoom, right-drag to move.</li>
<li>The original 2D plan in the top right corner. Click it to make it larger; the triangle folds it away.</li>
<li>Colours are added by the program, not taken from the drawing: <b>green</b> it is probably right, <b>orange</b> worth a look,
<b>red</b> check this. A switch on the left colours the model by element type instead.</li>
<li>The questions in the bottom right corner. The elements a question is about <b>blink yellow</b>. A gap in a wall
with no door is shown as a see-through orange box.</li></ul>

<h2>What we need from you</h2>
<table>
<tr><td>1. The questions<br><span>about 8 per plan</span></td><td>Press <b>Next</b>. Each question covers all elements
with the same doubt, for example "37 walls drawn as a single line". Answer <b>Correct</b>, <b>Wrong</b> or <b>Not sure</b>.
The next question follows by itself.<br>If you answer Wrong, please type in the comment box what is wrong and what it should be;
the page then waits for you.<br>If one element of a group is different from the rest, click it in the model and answer for that
one alone.</td></tr>
<tr><td>2. Not in the model</td><td>The last question lists things the program found on the drawing but could not place,
such as a door tag with no door. Tell us if it is acceptable to add these by hand.</td></tr>
<tr><td>3. Overall questions<br><span>2 minutes</span></td><td>After the last question they open on the left:
your role, how useful this is compared with redrawing the plan by hand (1 to 5), what you would not trust, what we should
change first, and any other remarks. Your name is optional.</td></tr>
<tr><td>4. Send the files</td><td>Email the feedback files (one per plan, named <b>feedback-&lt;plan&gt;.json</b>) to the
person who sent you this folder.</td></tr></table>

<h2>Where your answers are saved</h2><ul>
<li><b>Chrome and Edge:</b> on your first answer the browser asks where to save the feedback file. Choose a folder you will
find again, such as Desktop. Every answer is written to that file at once; the bottom right panel shows the file name and the time
of the last save.</li>
<li><b>Other browsers:</b> answers are kept in the browser. Press <b>Download feedback</b> at the end; the file goes to your
Downloads folder.</li>
<li>You can close a plan and come back later: your answers are still there. If the browser asks again to allow saving, click
Allow.</li></ul>

<p><span>Each plan folder also holds <b>model.ifc</b>, the model itself, if you want to open it in your own BIM software.</span></p>
</body></html>""".encode("utf-8")


def index_page(names, offline=False) -> bytes:
    """Start page of the website: each plan opens side by side. offline=True gives the emailed zip's page instead."""
    if offline:
        return offline_page(names)
    e = html.escape
    rows = "".join(f"<li><a href='{e(n)}/compare.html'>{e(n)}</a> <span>(<a href='{e(n)}/review.html'>review page only</a> · "
                   f"<a href='{e(n)}/viewer.html'>3D only</a>)</span></li>" for n in names)
    steps = ("<li>Open a plan. The drawing and a list of things to check are on the left, the 3D model on the right.</li>"
             "<li>For each item in \"To review\", press Correct, Wrong or Not sure, and add a comment if you like.</li>"
             "<li>At the bottom of the left side, answer four short questions and press Send feedback.</li>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>DXF to IFC: user test</title>{STYLE}</head><body>
<h1>From a 2D plan to a model you can check</h1>
<p>A program turned each drawing below into a 3D building model (IFC). We would like your opinion as an engineer.</p>
<div class="box"><b>What to do (about 10 minutes per plan)</b><ol>
{steps}</ol></div>
<h2>Plans</h2><ul>{rows or "<li>No plans yet.</li>"}</ul>
<p><span>On a small screen, use "review page only"; its "show in 3D" links open the model in a new tab.</span></p>
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
        if not (folder / "viewer.html").is_file():
            raise ValueError(f"{folder} has no viewer.html; run the plan first")
    top, files = ("dxf2ifc user test", ("viewer.html", "model.ifc")) if offline else ("site/plans", FILES)
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
