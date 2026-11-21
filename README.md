# dxf2ifc

Turns a 2D floor plan (DXF) into an IFC model. Every element carries its source in the drawing and a confidence level, and the tool writes a review page that lists what an engineer must check.

Runs on a laptop CPU. No GPU, no paid service. An LLM is optional and off by default.

## Set up (once)

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

Python 3.13 on Windows is what it was built and tested on.

## Convert one plan

```bash
.venv\Scripts\python -m dxf2ifc run plan.dxf --out out\plan
```

This writes seven files into `out\plan`:

| File | What it is |
|---|---|
| `model.ifc` | The IFC4 model. Elements are coloured by confidence in any viewer. |
| `model_ifc2x3.ifc` | The same model in IFC2X3, the format of SECO's reference models. |
| `review.html` | Open this first. The plan, the assumed values, the list of things to check, each with a link to it in 3D. Testers mark items correct or wrong and leave feedback at the bottom. |
| `viewer.html` | The model in 3D in the browser, coloured by confidence. Works offline (three.js is inside the file); buttons and keys for zoom and stepping through low-confidence elements. Click an element for its IFC id, rule and source. `viewer.html#<GlobalId>` jumps to one element. A switch shows simulated room sensors bound to each room's id. |
| `compare.html` | The review page and the 3D view side by side (open it through `serve` or `share`). |
| `plan.json` | The same content as plain data. |
| `roles.json` | What each drawing layer was taken to mean. |

To open the IFC in Bonsai Viewer: **Add Model › Add IFC File** ("Open Project" is for Bonsai's own project files, not IFC).

Useful options: `--elevation 7.48` and `--height 3.13` (storey level and height in metres), `--project NAME` (keeps element ids the same across revisions of a file), `--bbox xmin,ymin,xmax,ymax` (use one part of a sheet).

## Try it in a browser

```bash
.venv\Scripts\python -m dxf2ifc serve
```

Then open http://127.0.0.1:8000, choose a DXF, and press Convert. Everything stays on the computer.

## Several storeys in one building

```bash
.venv\Scripts\python -m dxf2ifc merge out\level1\plan.json out\level2\plan.json --out out\building.ifc
```

This also writes `building_ifc2x3.ifc` and a 3D view `building.html`. The top storey gets a flat roof, marked as an assumption (no floor plan shows the roof).

## User tests

Testers use the review page from the try-it server; their answers land in `out\web\feedback.jsonl`.

```bash
.venv\Scripts\python -m dxf2ifc feedback out\web\feedback.jsonl
```

How to run a test and where to record it: `docs/USER_TESTS.md`.

## Share with testers online

`share-pack` zips converted plans with a small website (review, 3D, side by side, feedback form with comments);
`share` runs the same site locally. Free hosting steps on PythonAnywhere: `docs/HOSTING.md`.

```bash
.venv\Scripts\python -m dxf2ifc share-pack out\plan_a out\plan_b --zip out\site.zip
```

## Other commands

| Command | Does |
|---|---|
| `bench` | Accuracy on synthetic plans where the true answer is known |
| `triage <folder-or-zip>` | Fast scan of many DXF files to find the floor plans |
| `shortlist data\dxf_index.csv` | Ranks scanned files and draws thumbnails |
| `batch data\dxf_index.csv` | Runs the pipeline on every plan-like file and records the results |
| `profile-ifc model.ifc --out profile.json` | Lists what an IFC file contains |
| `save-mapping out\plan\roles.json` | Keeps a corrected layer mapping for the next file with the same layers |

## Optional: an LLM for unknown layer names

Layers are classified by French, German and English keyword rules. For layers the rules can't place, one question can go to an LLM. It receives layer names, entity counts and a few text samples with every digit masked. It never receives geometry.

Create a file named `.env` in this folder:

```
LLM_PROVIDER=groq
LLM_MODEL=qwen/qwen3.8-27b
GROQ_API_KEY=<your key>
```

`LLM_PROVIDER` can be `groq`, `openrouter`, `ollama` (fully local) or `none`. The key can also be given as `LLM_API_KEY` or `OPENROUTER_API_KEY`. Answers are cached, so a second run on the same file costs nothing.

Tried on 19 Nov on a synthetic plan whose five layers have names no rule knows (`STRUCT_2D`, `MENUIS_INT`, ...):

| Provider and model (free tier) | Layers right | Time |
|---|---|---|
| none (rules only) | 1 of 5 | 0 s |
| groq `qwen/qwen3.8-27b` | 5 of 5 | 0.3 s |
| groq `openai/gpt-oss-20b` | 5 of 5 | 0.4 s |
| groq `openai/gpt-oss-120b` | 5 of 5 | 0.9 s |
| openrouter `nvidia/nemotron-3.5-lightning:free` | 5 of 5 | 20 s |
| openrouter `google/gemma-4-31b-it:free` | no usable answer | 6 s |

Before turning this on for a client's plans, check that client's rules on external AI. For SECO plans, use `ollama` or get SECO's agreement first.

## Tests

```bash
.venv\Scripts\python -m pytest -q
```

Tests use only synthetic drawings made by `tests/fixtures.py`.

## Data rule

No client drawing, and nothing derived from one, belongs in this repository. `out/`, `data/` and `docs/results.md` are ignored by git for that reason.

## How it works

1. **Normalise** (`normalize.py`): any DXF becomes segments and labels in metres; blocks are flattened.
2. **Layer roles** (`roles.py`): decide which layers hold walls, doors, text and so on. If no layer is named for walls, walls are picked by line weight.
3. **Extract** (`extract.py`): pairs of parallel lines become walls, gaps and door swings become openings, closed spaces become rooms named by the labels inside them. Stairs come from stair layers or from runs of treads; fire-safety symbols (extinguishers, detectors, exit signs) from fire layers.
4. **Write** (`to_ifc.py`): the IFC files, with a `SECO_Trust` property set on every element.
5. **Check and review** (`qa.py`, `review.py`, `viewer.py`): the minimum-requirements table, the review page and the 3D view.

Every threshold is in `dxf2ifc/config.py`. Known shortcuts are marked in the code with `# ponytail:`.
