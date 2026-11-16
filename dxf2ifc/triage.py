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


BINARY_SENTINEL = b"AutoCAD Binary DXF\r\n\x1a\x00"


def _value_sizes():
    """Bytes taken by the value of each group code in a binary DXF. -1 = text ending in a zero byte,
    -2 = one length byte followed by that many bytes."""
    sizes = [-1] * 1072
    for ranges, size in (
            (((10, 59), (110, 149), (210, 239), (460, 469), (1010, 1059)), 8),  # floating point
            (((60, 79), (170, 179), (270, 289), (370, 389), (400, 409), (1060, 1070)), 2),
            (((90, 99), (420, 429), (440, 459), (1071, 1071)), 4),
            (((160, 169),), 8),
            (((290, 299),), 1),
            (((310, 319), (1004, 1004)), -2)):
        for low, high in ranges:
            sizes[low:high + 1] = [size] * (high - low + 1)
    return sizes


SIZES = _value_sizes()


class _Tally:
    """What the scan keeps: entity counts, layer names, texts, block names, the drawing unit."""

    def __init__(self):
        self.counts, self.layers, self.blocks, self.texts = Counter(), set(), set(), []
        self.units = self.section = self.entity = self.variable = None
        self.name_next = False

    def text(self, code: int, value: bytes):
        """One text-valued tag. Only codes 0, 1, 2, 8 and 9 are passed in."""
        if code == 0:
            if value == b"SECTION":
                self.name_next, self.entity = True, None
            elif value == b"ENDSEC":
                self.section = None
            elif self.section in (b"ENTITIES", b"BLOCKS"):
                self.entity = value
                if value not in NOT_ENTITIES:
                    self.counts[value] += 1
        elif code == 2 and self.name_next:
            self.section, self.name_next = value, False
        elif self.section == b"HEADER":
            if code == 9:
                self.variable = value
        elif self.section in (b"ENTITIES", b"BLOCKS"):
            if code == 8:
                if len(self.layers) < 2000:
                    self.layers.add(value)
            elif code == 1:
                if self.entity in TEXT_TYPES and len(self.texts) < 300:
                    self.texts.append(value[:60])
            elif code == 2 and self.entity == b"INSERT" and len(self.blocks) < 500:
                self.blocks.add(value)

    def result(self) -> dict:
        decode = lambda raw: raw.decode("utf-8", errors="replace")
        return {"counts": {decode(k): v for k, v in self.counts.items()}, "units": self.units,
                "layers": sorted(decode(v) for v in self.layers), "blocks": sorted(decode(v) for v in self.blocks),
                "texts": [decode(v) for v in self.texts]}


def fast_scan(stream) -> dict:
    """Count entities, layers, texts and block names in a DXF, text or binary, without building objects."""
    data = stream.read()
    tally = _Tally()
    if data.startswith(BINARY_SENTINEL):
        pos, end, sizes, find = len(BINARY_SENTINEL), len(data), SIZES, data.find
        while pos + 2 <= end:
            code = data[pos] | data[pos + 1] << 8
            pos += 2
            size = sizes[code] if code < 1072 else -1
            if size > 0:
                if code == 70 and tally.variable == b"$INSUNITS":
                    tally.units, tally.variable = int.from_bytes(data[pos:pos + 2], "little", signed=True), None
                pos += size
            elif size == -1:
                stop = find(b"\x00", pos)
                if stop < 0:
                    break
                if code in (0, 1, 2, 8, 9):
                    tally.text(code, data[pos:stop])
                pos = stop + 1
            else:
                pos += 1 + data[pos]
    else:
        lines = data.split(b"\n")
        wanted = {b"0": 0, b"1": 1, b"2": 2, b"8": 8, b"9": 9}
        for i in range(0, len(lines) - 1, 2):
            code = lines[i].strip()
            if code in wanted:
                tally.text(wanted[code], lines[i + 1].strip())
            elif code == b"70" and tally.variable == b"$INSUNITS":
                tally.units, tally.variable = int(lines[i + 1].strip() or 0), None
    return tally.result()


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
