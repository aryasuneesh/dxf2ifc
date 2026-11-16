"""Run the pipeline over many DXF files and record one line of results per file.

This is the second triage pass and the dataset-wide measurement: a file is a floor plan the
method handles when its rooms close and their computed areas match the areas printed on it.

    python -m dxf2ifc.batch data/dxf_index.csv data/pipeline.csv --min-score 6
"""

import csv
import multiprocessing
import sys
import time
from collections import Counter
from pathlib import Path

from . import config

FIELDS = ["path", "error", "seconds", "family", "units", "segments", "walls", "single_line_walls", "doors",
          "windows", "gaps", "rooms_named", "rooms_unnamed", "rooms_with_area", "area_within_3", "area_within_5",
          "area_within_10", "coverage", "storey", "elevation", "review_flags"]


def run_one(path) -> dict:
    from .extract import extract
    from .normalize import load
    from .roles import assign_roles

    row = dict.fromkeys(FIELDS, "")
    row["path"] = str(path)
    start = time.perf_counter()
    try:
        drawing = load(path)
        plan = extract(drawing, assign_roles(drawing))
    except Exception as err:  # one bad file must not stop the batch
        row["error"] = f"{type(err).__name__}: {str(err)[:100]}"
        row["seconds"] = round(time.perf_counter() - start, 1)
        return row
    kinds = Counter(o["kind"] for o in plan["openings"])
    rooms = plan["rooms"]
    checked = [abs(r["area_m2"] - r["area_printed_m2"]) / r["area_printed_m2"] for r in rooms if r["area_printed_m2"]]
    stats = plan["stats"]
    row.update(
        seconds=round(time.perf_counter() - start, 1), family=plan["source"]["family"], units=drawing.units_in,
        segments=len(drawing.segments), walls=len(plan["walls"]),
        single_line_walls=sum(w["rule"] != "parallel_pair" for w in plan["walls"]),
        doors=kinds["door"], windows=kinds["window"], gaps=kinds["opening"],
        rooms_named=sum(r["rule"] == "flood_fill" for r in rooms),
        rooms_unnamed=sum(r["rule"] != "flood_fill" for r in rooms),
        rooms_with_area=len(checked), area_within_3=sum(d < 0.03 for d in checked),
        area_within_5=sum(d < 0.05 for d in checked), area_within_10=sum(d < 0.10 for d in checked),
        coverage=round(stats["wall_explained_length_m"] / stats["wall_layer_length_m"], 2) if stats["wall_layer_length_m"] else "",
        storey=plan["storey"]["name"][:40], elevation=plan["storey"]["elevation_m"],
        review_flags=sum(f["severity"] == "review" for f in plan["flags"]))
    return row


def run_batch(paths, out_csv, workers=None) -> int:
    """Files already in out_csv are skipped, so a stopped run can be continued."""
    out_csv = Path(out_csv)
    done = set()
    if out_csv.is_file():
        with out_csv.open(newline="", encoding="utf-8") as handle:
            done = {row["path"] for row in csv.DictReader(handle)}
    todo = [str(p) for p in paths if str(p) not in done]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fresh = not out_csv.is_file()
    with out_csv.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if fresh:
            writer.writeheader()
        if workers == 1 or len(todo) < 4:
            pool, results = None, map(run_one, todo)
        else:
            pool = multiprocessing.Pool(workers, maxtasksperchild=20)  # big drawings leave big heaps behind
            results = pool.imap_unordered(run_one, todo, chunksize=1)
        for n, row in enumerate(results, 1):
            writer.writerow(row)
            handle.flush()
            if n % 50 == 0:
                print(f"  {n} / {len(todo)}", file=sys.stderr, flush=True)
        if pool:
            pool.close()
            pool.join()
    return len(todo)


if __name__ == "__main__":
    import argparse

    from .triage import read_index

    parser = argparse.ArgumentParser(prog="python -m dxf2ifc.batch")
    parser.add_argument("index")
    parser.add_argument("out")
    parser.add_argument("--min-score", type=int, default=config.TRIAGE_PLAN_SCORE)
    parser.add_argument("--max-mb", type=float, default=config.BATCH_MAX_MB)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()
    chosen = [row["path"] for row in read_index(args.index)
              if not row["error"] and int(row["score"]) >= args.min_score and float(row["size_mb"]) <= args.max_mb]
    print(f"{len(chosen)} files selected")
    print(f"ran {run_batch(chosen, args.out, args.workers)} files into {args.out}")
