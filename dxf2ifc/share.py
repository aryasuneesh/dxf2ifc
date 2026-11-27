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


def index_page(names) -> bytes:
    """Start page of the website: each plan opens side by side."""
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
    Default: for hosting, with this file as the website. offline=True: the whole prototype to email: START HERE is
    the tool's home page with these plans, and start.bat runs the converter itself (upload your own DXF)."""
    folders = [Path(f) for f in plan_dirs]
    for folder in folders:
        if not (folder / "viewer.html").is_file():
            raise ValueError(f"{folder} has no viewer.html; run the plan first")
    top = "dxf2ifc prototype"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        if not offline:
            archive.write(__file__, "site/share.py")
            for folder in folders:
                for name in FILES:
                    if (folder / name).is_file():
                        archive.write(folder / name, f"site/plans/{folder.name}/{name}")
            return [f.name for f in folders]
        from .cli import PAGE, run_card
        cards = [dict(card, id=folder.name) for folder in folders if (card := run_card(folder))]
        data = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")  # no early end of the script tag
        page = PAGE.replace("const STATIC = null;", f"const STATIC = {data};")
        archive.writestr(f"{top}/START HERE.html", page.replace("__ROOT__", "app\\out\\web"))
        archive.writestr(f"{top}/start.bat", START_BAT)
        for folder in folders:
            for name in (*FILES, "plan.png"):
                if (folder / name).is_file():
                    archive.write(folder / name, f"{top}/plans/{folder.name}/{name}")
        package = Path(__file__).parent  # the converter, as in the repository; no data, no keys
        for path in sorted(package.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, f"{top}/app/dxf2ifc/{path.relative_to(package).as_posix()}")
        for name in ("requirements.txt", "README.md", "LICENSE"):
            if (package.parent / name).is_file():
                archive.write(package.parent / name, f"{top}/app/{name}")
    return [f.name for f in folders]


# Runs the converter from the emailed folder: makes its own Python environment once, then opens the home page.
START_BAT = r"""@echo off
title DXF to IFC
cd /d "%~dp0app"
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY (
  echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ then run start.bat again.
  pause & exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo First start: installing the converter. This takes a few minutes and needs internet.
  %PY% -m venv .venv || goto failed
  .venv\Scripts\python -m pip install --disable-pip-version-check -q -r requirements.txt || goto failed
)
echo The home page opens in your browser. Close this window to stop the converter.
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000/"
.venv\Scripts\python -m dxf2ifc serve --port 8000
pause
exit /b 0
:failed
echo Installing failed. Check the internet connection and that Python is 3.10 or newer, then run start.bat again.
pause
exit /b 1
""".replace("\n", "\r\n")


def serve(root, port=8000, password=None, host="127.0.0.1"):
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    class Quiet(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    return make_server(host, port, make_app(root, password), handler_class=Quiet)
