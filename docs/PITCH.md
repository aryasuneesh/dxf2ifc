# Pitch outline (30 Nov: 5 min demo + 5 min Q&A)

Draft. To be finished after the pitch workshop (week 4) and the user tests.
Numbers marked [run] come from the latest dataset run in `STATUS.md`; numbers marked [test] come from user tests.

## The story in one line

SECO's engineers receive clients' 2D DXF plans and need a BIM model to check them. Redrawing takes days.
We turn the DXF into an IFC in seconds, and we show the engineer exactly what to check.

## Slides and timing

| Time | Slide | What we say |
|---|---|---|
| 0:00 | **Problem** | Who: a SECO control engineer. Pain: plans arrive as 2D DXF, structured differently by every client, often with layers named only by colour (Martin: "poorly annotated DXF files"). A model is redrawn by hand before any check can start. |
| 0:40 | **What we learned** | Interviews with Martin: DXF only; a model is never perfect, so "explain what a person should review"; no client plan may go to outside AI; define and justify your own quality bar. |
| 1:10 | **Our answer** | DXF in, IFC4 (and IFC2X3) out, plus a review page. Every element carries its source in the drawing, a confidence and "To be verified". |
| 1:30 | **Live demo** (2 min) | 1. Upload a DXF on the local page. 2. Review page: the plan coloured by confidence, the "To review" list with a crop of each doubt. 3. Click "show in 3D": the element in the browser viewer, with its IFC id, rule and source. 4. Turn on simulated room sensors: the stable room id is where a portal or IoT value attaches. |
| 3:30 | **Does it work?** | We set the bar ourselves (R1–R12) and measure it on SECO's whole dataset, not on one hand-picked plan: 15,971 files scanned, 2,599 of them floor plans, 2,591 converted without error, rooms checked against the areas printed on the plans themselves. Honest number: 50 % of 5,808 checked rooms within 5 % of the printed area (45 % a week earlier). Where it fails, the page says so. |
| 4:00 | **Users tried it** | [test] Who tested, what they said, what we changed, before and after. |
| 4:30 | **Ready to industrialise** | Plain Python, CPU only, about 2 s per plan (median), zero API cost. Runs behind an API on Azure or Databricks as is. Optional LLM step for unknown layer names: free tier or fully local (Ollama), never sees geometry. MIT licence. |
| 4:50 | **Ask** | A pilot on one live SECO project, with one engineer marking the review page. |

## Demo checklist

- Plan to use: one that passes all checks (see local `data/demo_candidates.md`), plus one that does not, to show the flags.
- Local try-it page running (`python -m dxf2ifc serve`), 3D view tested on the demo laptop, internet for the 3D library (or a cached copy).
- Backup: screen recording of the whole demo, and screenshots in the slides.

## Likely questions

| Question | Answer |
|---|---|
| How accurate is it against the reference IFC? | The reference model SECO gave us has no matching DXF in the dataset, so we measure against the plans' own printed room areas instead, on every plan. Give us one matching DXF and IFC pair and the comparison script runs on it. |
| What about scanned or PDF plans? | Out of scope by SECO's decision (DXF only). The pipeline starts from lines and texts, so a vectorising step in front of it would add PDFs. |
| Why rules and not machine learning? | No labelled training data, two weeks, and every decision has to be explainable on the review page. Rules give a reason per element; the LLM step is there for the open-ended part (layer names). |
| Does client data leave SECO? | No. Everything runs locally. The optional LLM step sends layer names and masked text only, and can run on a local model. |
| What does an engineer still do? | Checks the "To review" list: typically door heights (rarely on the plan), storey height, low-confidence walls, rooms whose area differs from the printed one. |
| What does it cost per plan? | CPU seconds, no licence, no API fee. |
| What is missing? | Steps inside stairs, sloped roofs, columns and structural detail, correction inside the tool. Each is flagged, not guessed. |

## Still to do

- Fill the [test] numbers.
- Choose the two demo plans.
- Slides (after the pitch workshop).
- Rehearse with a timer twice.
