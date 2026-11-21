# Status and work log

What the hackathon asks for, what this repo delivers, and what is still open. Updated as work lands.

This repo is public. It holds code and synthetic test drawings only. SECO plans, the dataset,
per-file results and anything derived from them stay local (`data/`, `docs/results.md`, `out/`).
Numbers below are dataset-wide totals only, with no file, project or room names.

## 1. Deliverables checklist

Status: **done**, **partly** (works, with a known limit), **open** (not started), **blocked** (needs something from outside the code), **won't** (out of scope by decision).

### Week 2: design a concept (before session 3)

| Step | Status | Where |
|---|---|---|
| Choose an idea (brainstorm, group, vote on impact and feasibility) | done | `PLAN.md` §1 (team folder, not in this repo) |
| Define the concept, at most 3 MUSTs | done | `PLAN.md` §1.2: valid IFC, trust layer, measured quality |
| Prototype: a rough version | done | this repo; `python -m dxf2ifc run plan.dxf` |
| Try it for 10 minutes with an outsider | **open (team)** | tooling ready: feedback form on the review page, protocol in `USER_TESTS.md` |

### Week 3: build, test, build, test

| Step | Status | Where |
|---|---|---|
| Test with the real user profile (engineers), collect feedback | **open (team)** | `USER_TESTS.md`: who, how, where answers land; `python -m dxf2ifc feedback` sums them up |
| Iterate on what they say, record before and after | open | one block per round in `USER_TESTS.md` |

### The three MUSTs

| MUST | Status | Evidence |
|---|---|---|
| 1. Valid IFC from a DXF: storeys, walls, slabs, rooms (name and area), doors, with drawing metadata | done | IFC4 and IFC2X3 both written and validated; tests in `test_to_ifc.py` |
| 2. Trust layer: source, confidence and "To be verified" on every element, plus a review checklist | done | `SECO_Trust` property set; `review.html` with crops and links to 3D |
| 3. Measured quality against minimum requirements we define and justify | partly | R1–R12 in `qa.py`; room areas checked against printed areas on the whole dataset (§2). **Comparison with the reference IFC is not possible**: no DXF in the dataset matches it, and SECO is not expected to provide one |

### SECO's spec (kickoff, "what a great answer looks like")

| Spec item | Status | Notes |
|---|---|---|
| Input: DXF | done | text and binary DXF, m/cm/mm/in, blocks and xrefs flattened |
| Input: PDF, scans, DWG | won't | Martin, 15 Nov: DXF only |
| FR/DE/EN labels | done | layer and room word rules, AIA layer names (`A-WALL`, `S-STRS`, ...) |
| IFC4 with storeys, walls, slabs, doors, windows, named rooms with areas | done | IFC2X3 copy too, matching SECO's reference model |
| Stairs | done | outline block from stair layers or from runs of treads; flagged "outline only, no steps" |
| Roofs | partly | flat roof over the top storey when storeys are merged, flagged as an assumption; sloped roofs need a roof plan |
| Every element links to its source and a confidence | done | DXF handles, layer, rule, confidence in `SECO_Trust` |
| Uncertain means flagged, not guessed | done | defaults and low-confidence elements listed on the review page |
| Engineer can review and fix quickly | partly | review page says what to check, with a crop and a 3D link for each item; testers mark correct or wrong. Geometry is fixed in a BIM tool, not here |
| Viewable in a browser, stable IDs | done | `viewer.html`: 3D, coloured by confidence, click for IFC id and source; `viewer.html#<GlobalId>` opens one element; ids are the same on every run |
| Deployable on Azure / Databricks behind an API, cost per plan | partly | plain Python, CPU only, ~2 s median per plan, no paid API; local web page with an upload endpoint. No container or cloud deployment yet |
| Open-source code and weights | done | MIT licence; no model weights (rules, optional open LLM) |
| No SECO plan sent to external AI | done | LLM step off by default; when on, it sends layer names and masked text only, never geometry; can run locally with Ollama |
| Extra: simulated sensor value on a room | done | switch in the 3D view: temperature and CO2 per room, bound to the room's IFC id, labelled "simulated" |
| Extra: fire-safety symbols | done | extinguishers, detectors and exit signs from fire layers, written as typed IFC proxies |

### Judging weights (kickoff)

| Criterion | Weight | Where we stand |
|---|---|---|
| Measured quality on SECO plans vs reference IFC | 35 % | self-check against printed room areas on every plan (§2); reference comparison not possible |
| Usefulness for engineers and clients | 25 % | review page, 3D view, feedback loop built; **user tests not yet run** |
| Robustness | 15 % | 2,599 plan-like dataset files, 2,591 finish without error |
| Industrialisation | 15 % | ~2 s CPU per plan, zero API cost, MIT; container and API story to write |
| Originality and pitch | 10 % | outline in `PITCH.md`; slides after the pitch workshop |

## 2. Current numbers (whole dataset, 19 Nov)

| | 16 Nov | 19 Nov |
|---|---|---|
| DXF files scanned | 15,971 | 15,971 |
| Plan-like files run end to end (up to 25 MB) | 2,599 | 2,599 |
| Finished without error | 2,576 | 2,591 |
| Median wall-line coverage | 63 % | 84 % |
| Files with at least one named room | 976 | 1,099 |
| Rooms checked against a printed area | 4,155 | 5,808 |
| Rooms within 3 % of printed area | 35 % | 44 % |
| Rooms within 5 % | 45 % (1,863) | 50 % (2,883) |
| Rooms within 10 % | 56 % | 57 % |
| Files passing the room-area requirement (R9) | 77 | 81 |
| Median time per plan | 3.7 s | 2.0 s |
| Fire-safety symbols found | – | 1,744 in 168 files |
| Stairs found (outline blocks) | – | 7,033 in 1,607 files |

## 3. Open items

1. **User tests** with engineers and an outsider, then one round of changes from what they say. Needs the team.
2. **Pitch**: slides after the pitch workshop next week; fill the numbers into `PITCH.md`.
3. **Reference IFC**: no matching DXF; not expected. The printed-area check stands in for it.
4. **IFC files** load in Bonsai Viewer 0.9.0 (IFC4 and IFC2X3, 21 Nov); a person still has to check them element by element, and Revit is untested.
5. **Deployment story** for Azure / Databricks (container + API) is not written.
6. ~~3D view needs internet~~: three.js is now inside the page (21 Nov).

## 4. Log

- **15 Nov**: plan and split into 8 sections; scaffold, plan contract (`plan.json`), synthetic test house; DXF loading with block flattening and label joining; layer roles from name rules, evidence and an optional LLM.
- **16 Nov**: walls, openings, door swings, rooms, storey and slab; IFC4 writer with trust properties and stable IDs; dataset triage (text and binary DXF); batch runner; requirement checks R1–R12 and review page; command line, storey merge and local try-it page; README. First dataset run.
- **19 Nov, morning**: room fixes. Raster bias removed (rooms came out ~2 % small). Buildings drawn in loose pieces are kept whole (one sheet had lost 1,218 walls). Window lines close glazed facades. AIA hatch (`-PATT`) and column (`COLS`) layers recognised. Repo on GitHub.
- **19 Nov, afternoon**: MIT licence. LLM step tested on free tiers with a synthetic plan (Groq `qwen/qwen3.8-27b`: 5 of 5 layers right in 0.3 s; rules alone: 1 of 5). IFC2X3 written next to IFC4. 3D browser viewer with confidence colours, element details and links by GlobalId. Stairs (stair layers, tread runs) and an assumed flat roof on merged buildings. Fire-safety symbols. Simulated room sensors. Correct/Wrong buttons and a feedback form on the review page, `feedback` command, user-test protocol. Pitch outline. Dataset re-run from a frozen copy of the code (§2).
- **21 Nov**: pitch numbers filled; demo plans chosen and re-run; draft slides. Plan and 3D side by side (`compare.html`). IFC loads in Bonsai Viewer. 3D view works offline, with buttons and keys. Review page easier to read: section links, how-to box, Not sure button, comment per item, general remarks, marks kept in the browser until sent. Test website (`share`, `share-pack`) with password and feedback file, hosting steps for free PythonAnywhere in `HOSTING.md`.
