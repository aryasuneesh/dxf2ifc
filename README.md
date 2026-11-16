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

This writes four files into `out\plan`:

| File | What it is |
|---|---|
| `model.ifc` | The IFC4 model. Elements are coloured by confidence in any viewer. |
| `review.html` | Open this first. The plan, the assumed values, the list of things to check. |
| `plan.json` | The same content as plain data. |
| `roles.json` | What each drawing layer was taken to mean. |

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
LLM_MODEL=<a model id from the provider>
LLM_API_KEY=<your key>
```

`LLM_PROVIDER` can be `groq`, `openrouter`, `ollama` (fully local) or `none`. Answers are cached, so a second run on the same file costs nothing.

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
3. **Extract** (`extract.py`): pairs of parallel lines become walls, gaps and door swings become openings, closed spaces become rooms named by the labels inside them.
4. **Write** (`to_ifc.py`): the IFC file, with a `SECO_Trust` property set on every element.
5. **Check and review** (`qa.py`, `review.py`): the minimum-requirements table and the review page.

Every threshold is in `dxf2ifc/config.py`. Known shortcuts are marked in the code with `# ponytail:`.
