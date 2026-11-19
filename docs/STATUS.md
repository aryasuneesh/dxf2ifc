# Status and work log

What the hackathon asks for, what this repo delivers, and what is still open. Updated as work lands.

This repo is public. It holds code and synthetic test drawings only. SECO plans, the dataset,
per-file results and anything derived from them stay local (`data/`, `docs/results.md`, `out/`).
Numbers below are dataset-wide totals only, with no file, project or room names.

## 1. Deliverables checklist

Status: **done**, **partly** (works, with a known limit), **open** (not started), **blocked** (needs something from outside the code).

### Week 2: design a concept (before session 3)

| Step | Status | Where |
|---|---|---|
| Choose an idea (brainstorm, group, vote on impact and feasibility) | done | `PLAN.md` §1 (team folder, not in this repo) |
| Define the concept, at most 3 MUSTs | done | `PLAN.md` §1.2: valid IFC4, trust layer, measured quality |
| Prototype: a rough version | done | this repo; `python -m dxf2ifc run plan.dxf` |
| Try it for 10 minutes with an outsider | **open** | needs a team member and an outsider; protocol in §4 below |

### The three MUSTs

| MUST | Status | Evidence |
|---|---|---|
| 1. Valid IFC4 from a DXF: storeys, walls, slabs, rooms (name and area), doors, with drawing metadata | done | `to_ifc.py`; validates with `ifcopenshell.validate`; test `test_to_ifc.py` |
| 2. Trust layer: source, confidence and "To be verified" on every element, plus a review checklist | done | `SECO_Trust` property set; `review.html`; test `test_qa.py` |
| 3. Measured quality against minimum requirements we define and justify | partly | R1–R12 checks in `qa.py`, room areas checked against printed areas on the whole dataset. **Scoring against the reference IFC is blocked**: no DXF in the dataset matches it |

### SECO's spec (kickoff, "what a great answer looks like")

| Spec item | Status | Notes |
|---|---|---|
| Input: DXF | done | text and binary DXF, m/cm/mm/in, blocks and xrefs flattened |
| Input: PDF, scans, DWG | won't | Martin, 15 Nov: DXF only |
| FR/DE/EN labels | done | layer and room word rules in `config.py` |
| IFC4 storeys, walls, slabs, doors, windows, named rooms with areas | done | |
| Stairs, roofs | open | stair layers are detected and reported as "not modelled" |
| Every element links to its source and a confidence | done | DXF handles, layer, rule, confidence in `SECO_Trust` |
| Uncertain means flagged, not guessed | done | defaults and low-confidence elements listed on the review page |
| Engineer can review and fix quickly | partly | review page says what to check and where; layer roles can be corrected (`save-mapping`). Geometry is fixed in a BIM tool, not here |
| Viewable in a browser, stable IDs | partly | stable GlobalIds (same on every run); 2D review page in the browser. **No 3D web viewer yet** |
| Deployable on Azure / Databricks behind an API, cost per plan | partly | plain Python, no GPU, no paid API; local web page (`serve`). No container or cloud deployment yet |
| Open-source code and weights | partly | code is here; **no licence file yet**; no model weights (rules, optional open LLM) |
| No SECO plan sent to external AI | done | LLM step is off by default; when on, it sends layer names and masked text only, never geometry |
| Extras: simulated sensor on a room, fire-safety symbols | open | |

### Judging weights (kickoff)

| Criterion | Weight | Where we stand |
|---|---|---|
| Measured quality on SECO plans vs reference IFC | 35 % | self-check on printed room areas works everywhere; reference comparison blocked (see §3) |
| Usefulness for engineers and clients | 25 % | review page, confidence colours; web 3D view open; no engineer feedback yet |
| Robustness | 15 % | run on 2,599 plan-like dataset files, 99 % finish without error |
| Industrialisation | 15 % | ~4 s CPU per plan, zero API cost; deployment story to write |
| Originality and pitch | 10 % | pitch not started |

## 2. Current numbers (whole dataset)

| | |
|---|---|
| DXF files scanned | 15,971, all readable |
| Plan-like files (first pass) | 2,863 |
| Run end to end (files up to 25 MB) | 2,599; 2,576 without error |
| Median time per plan | 3.7 s |

Room areas against the areas printed on the plans, 120-plan sample, 19 Nov:

| | Before fixes | After fixes |
|---|---|---|
| Rooms checked | 1,110 | 1,585 |
| Within 5 % | 515 (46 %) | 799 (50 %) |

Full-dataset re-run with the 19 Nov fixes: pending.

## 3. Open items and blockers

1. **Reference IFC has no matching DXF.** Ask SECO for the DXF(s) of that project, or an IFC that matches a dataset DXF. Without it the 35 % criterion is self-check only.
2. **IFC4 or IFC2X3?** The reference is IFC2X3; we write IFC4. Ask SECO.
3. **Outsider test** (week 2 step 3): not run.
4. **3D web viewer** (SHOULD): not built.
5. **Licence**: none yet; the spec asks for open source.
6. **LLM step**: never run against a real provider.
7. **IFC files** not yet opened in Bonsai or Revit by a person.
8. **Pitch** (30 Nov, 5 min demo + 5 min Q&A): not started.

## 4. Outsider test protocol (10 minutes)

1. Give the tester the review page of one plan and the IFC open in a viewer. Say only: "This was made from a 2D drawing. Find what you would not trust."
2. Watch, do not help. Note where they hesitate and what they click.
3. Ask: what would you check first? Is anything on the page unclear? Would you use this instead of redrawing?
4. Write down three findings and one change. Add them to the log below.

## 5. Log

- **15 Nov**: plan and split into 8 sections; scaffold, plan contract (`plan.json`), synthetic test house; DXF loading with block flattening and label joining; layer roles from name rules, evidence and an optional LLM.
- **16 Nov**: walls, openings, door swings, rooms, storey and slab; IFC4 writer with trust properties and stable IDs; dataset triage (text and binary DXF); batch runner; requirement checks R1–R12 and review page; command line, storey merge and local try-it page; README. First dataset run: room areas within 5 % of printed for 45 % of 4,155 checked rooms.
- **19 Nov**: room fixes. Raster bias removed (rooms came out ~2 % small). Buildings drawn in loose pieces are kept whole (one sheet had lost 1,218 walls). Window lines close glazed facades. AIA-style hatch (`-PATT`) and column (`COLS`) layers recognised. Sample result above. Repo moved to GitHub.
