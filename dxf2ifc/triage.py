"""Find the floor plans in a large pile of DXF files without parsing each one fully.

A DXF is text made of line pairs (a group code, then a value). The scan below only counts
things, which is enough to rank files and runs at disk speed.

    python -m dxf2ifc.triage scan <folder-or-zip> data/index.csv
    python -m dxf2ifc.triage shortlist data/index.csv --n 30

# ponytail: the score is a hand-set point table. If the shortlist is poor, train a small
# classifier on the files someone has labelled y/n in data/shortlist.md.
"""

import csv
import io
import multiprocessing
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from . import config

FIELDS = ["path", "size_mb", "units", "n_entities", "n_line", "n_lwpolyline", "n_insert", "n_text", "n_hatch",
          "n_dimension", "n_layers", "project_id", "score", "signals", "layers_sample", "texts_sample",
          "ref_hit", "error"]
TEXT_TYPES = (b"TEXT", b"MTEXT", b"ATTRIB")
NOT_ENTITIES = (b"BLOCK", b"ENDBLK", b"SEQEND", b"VERTEX")
AREA_RE = re.compile(r"\d+[.,]\d+\s*m\s?[2²]", re.IGNORECASE)


def fast_scan(stream) -> dict:
    """Count entities, layers, texts and block names from a binary DXF stream, pair by pair."""
    counts, layers, blocks, texts = Counter(), set(), set(), []
    units = section = entity = variable = None
    name_next = False
    lines = iter(stream)
    first = next(lines, b"")
    if first.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("binary")
    code = first
    while True:
        value = next(lines, None)
        if value is None:
            break
        code, value = code.strip(), value.strip()
        if code == b"0":
            if value == b"SECTION":
                name_next, entity = True, None
            elif value == b"ENDSEC":
                section = None
            elif section in (b"ENTITIES", b"BLOCKS"):
                entity = value
                if value not in NOT_ENTITIES:
                    counts[value] += 1
        elif name_next and code == b"2":
            section, name_next = value, False
        elif section == b"HEADER":
            if code == b"9":
                variable = value
            elif variable == b"$INSUNITS" and code == b"70":
                units = int(value or 0)
        elif section in (b"ENTITIES", b"BLOCKS"):
            if code == b"8":
                if len(layers) < 2000:
                    layers.add(value)
            elif code == b"1":
                if entity in TEXT_TYPES and len(texts) < 300:
                    texts.append(value[:60])
            elif code == b"2" and entity == b"INSERT" and len(blocks) < 500:
                blocks.add(value)
        code = next(lines, None)
        if code is None:
            break
    decode = lambda raw: raw.decode("utf-8", errors="replace")
    return {"counts": {decode(k): v for k, v in counts.items()}, "units": units,
            "layers": sorted(decode(v) for v in layers), "blocks": sorted(decode(v) for v in blocks),
            "texts": [decode(v) for v in texts]}


def score(scan: dict) -> tuple[int, list[str]]:
    """How much the file looks like a floor plan. Returns points and the signals that fired."""
    layers = [name.rsplit("$0$", 1)[-1].lower() for name in scan["layers"]]
    names = layers + [name.lower() for name in scan["blocks"]]
    texts = [text.lower() for text in scan["texts"]]
    counts = scan["counts"]
    hit = lambda pattern, items: sum(bool(re.search(pattern, item)) for item in items)
    wall = hit(config.TRIAGE_WALL, layers)
    signals = [
        ("wall_layer", 3, wall > 0),
        ("door_window", 2, hit(config.TRIAGE_OPENING, names) > 0),
        ("room_words", 3, hit(rf"(^|[^a-zà-ü])({config.ROOM_WORDS})", set(texts)) >= 3),
        ("areas", 3, sum(bool(AREA_RE.search(t)) for t in texts) >= 3),
        ("storey_word", 1, hit(config.STOREY_WORDS, texts) > 0),
        ("many_lines", 1, sum(counts.get(k, 0) for k in ("LINE", "LWPOLYLINE", "POLYLINE")) >= 500),
        ("survey", -3, hit(config.TRIAGE_SURVEY, layers) > 0),
        ("tiny", -3, sum(counts.values()) < 100),
        ("section_or_site", -2, hit(config.TRIAGE_SECTION, names) > 0 and not wall),
    ]
    fired = [(name, points) for name, points, on in signals if on]
    return sum(points for _, points in fired), [name for name, _ in fired]


def _row(task) -> dict:
    """Scan one file. task = (zip path or None, file path or member name, size in bytes)."""
    archive, name, size = task
    row = dict.fromkeys(FIELDS, "")
    stem = Path(name).stem
    row.update(path=name, size_mb=round(size / 1e6, 2), project_id=stem.rsplit("_", 1)[0])
    try:
        if archive:
            with zipfile.ZipFile(archive) as zf, zf.open(name) as stream:
                scan = fast_scan(stream)
        else:
            with open(name, "rb", buffering=1 << 20) as stream:
                scan = fast_scan(stream)
    except Exception as err:  # one bad file must not stop the run
        row["error"] = str(err)[:80] or type(err).__name__
        return row
    counts = scan["counts"]
    points, signals = score(scan)
    everything = " ".join([name] + scan["layers"] + scan["texts"])
    row.update(
        units=scan["units"] if scan["units"] is not None else "",
        n_entities=sum(counts.values()), n_line=counts.get("LINE", 0), n_lwpolyline=counts.get("LWPOLYLINE", 0),
        n_insert=counts.get("INSERT", 0), n_text=sum(counts.get(k, 0) for k in ("TEXT", "MTEXT")),
        n_hatch=counts.get("HATCH", 0), n_dimension=counts.get("DIMENSION", 0), n_layers=len(scan["layers"]),
        score=points, signals=" ".join(signals),
        layers_sample=" | ".join(scan["layers"][:25])[:400], texts_sample=" | ".join(scan["texts"][:25])[:400],
        ref_hit=int(any(code in everything for code in config.TRIAGE_REF_CODES)))
    return row


def _tasks(source: Path):
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            return [(str(source), info.filename, info.file_size) for info in zf.infolist()
                    if info.filename.lower().endswith(".dxf")]
    return [(None, str(path), path.stat().st_size) for path in sorted(source.rglob("*"))
            if path.suffix.lower() == ".dxf" and path.is_file()]


def read_index(csv_path) -> list[dict]:
    path = Path(csv_path)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def triage(source, out_csv, workers=None) -> int:
    """Scan every DXF under a folder or inside a zip into one CSV. Files already in the CSV are skipped."""
    out_csv = Path(out_csv)
    done = {row["path"] for row in read_index(out_csv)}
    tasks = [task for task in _tasks(Path(source)) if task[1] not in done]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fresh = not out_csv.is_file()
    with out_csv.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if fresh:
            writer.writeheader()
        if workers == 1 or len(tasks) < 4:
            results = map(_row, tasks)
            pool = None
        else:
            pool = multiprocessing.Pool(workers)
            results = pool.imap_unordered(_row, tasks, chunksize=4)
        for n, row in enumerate(results, 1):
            writer.writerow(row)
            if n % 200 == 0:
                handle.flush()
                print(f"  {n} / {len(tasks)}", file=sys.stderr, flush=True)
        if pool:
            pool.close()
            pool.join()
    return len(tasks)


def shortlist(csv_path, n=30) -> tuple[list[dict], list[tuple[str, int, int]]]:
    """Top n files by score, and the projects with the highest summed score of plan-like files."""
    rows = [row for row in read_index(csv_path) if not row["error"]]
    for row in rows:
        row["score"] = int(row["score"])
    rows.sort(key=lambda row: (-row["score"], -float(row["size_mb"])))
    projects = defaultdict(lambda: [0, 0])
    for row in rows:
        if row["score"] >= config.TRIAGE_PLAN_SCORE:
            projects[row["project_id"]][0] += row["score"]
            projects[row["project_id"]][1] += 1
    ranked = sorted(((name, total, count) for name, (total, count) in projects.items()), key=lambda p: -p[1])
    return rows[:n], ranked[:10]


def thumbnail(dxf_path, png_path, size=900) -> bool:
    """All line work of one file in black, for a person to judge at a glance."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    from .normalize import load
    try:
        drawing = load(dxf_path)
    except ValueError:
        return False
    fig, ax = plt.subplots(figsize=(size / 100, size / 100))
    ax.add_collection(LineCollection([[(s.x1, s.y1), (s.x2, s.y2)] for s in drawing.segments],
                                     colors="black", linewidths=0.3))
    ax.set_aspect("equal")
    ax.autoscale()
    ax.axis("off")
    Path(png_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return True


def write_shortlist(csv_path, out_md, n=30, thumbs_dir=None) -> list[dict]:
    top, projects = shortlist(csv_path, n)
    rows = read_index(csv_path)
    plan_like = sum(1 for row in rows if not row["error"] and int(row["score"]) >= config.TRIAGE_PLAN_SCORE)
    lines = ["# Floor-plan shortlist", "",
             f"{len(rows)} files scanned, {sum(1 for r in rows if r['error'])} unreadable, "
             f"{plan_like} score {config.TRIAGE_PLAN_SCORE} or more.", "",
             "| # | File | Score | Signals | MB | Thumbnail | Floor plan? (y/n) |", "|---|---|---|---|---|---|---|"]
    for i, row in enumerate(top, 1):
        thumb = ""
        if thumbs_dir and float(row["size_mb"]) <= config.TRIAGE_THUMB_MAX_MB:
            png = Path(thumbs_dir) / f"{Path(row['path']).stem}.png"
            if thumbnail(row["path"], png):
                thumb = png.name
        lines.append(f"| {i} | {Path(row['path']).name} | {row['score']} | {row['signals']} | {row['size_mb']} | {thumb} | |")
    lines += ["", "## Projects with the most plan-like files", "", "| Project | Summed score | Files |", "|---|---|---|"]
    lines += [f"| {name} | {total} | {count} |" for name, total, count in projects]
    Path(out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return top


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="python -m dxf2ifc.triage")
    sub = parser.add_subparsers(dest="command", required=True)
    scan_args = sub.add_parser("scan")
    scan_args.add_argument("source")
    scan_args.add_argument("csv")
    scan_args.add_argument("--workers", type=int, default=None)
    short_args = sub.add_parser("shortlist")
    short_args.add_argument("csv")
    short_args.add_argument("--n", type=int, default=30)
    short_args.add_argument("--out", default="data/shortlist.md")
    short_args.add_argument("--thumbs", default="data/thumbs")
    args = parser.parse_args()
    if args.command == "scan":
        print(f"scanned {triage(args.source, args.csv, args.workers)} new files into {args.csv}")
    else:
        for row in write_shortlist(args.csv, args.out, args.n, args.thumbs):
            print(row["score"], row["signals"], Path(row["path"]).name)
