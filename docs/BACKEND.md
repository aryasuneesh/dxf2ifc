# Storing the data: backend options (theory, not built)

The engineers asked how the data would be stored once dxf2ifc runs for a whole office, not one laptop.
Nothing here is integrated. Today every run writes files to one folder (`plan.dxf`, `plan.json`,
`model.ifc`, `review.json`, `feedback.jsonl`); this page is what would replace that folder.

## What has to be stored

| Data | Shape | Size per plan | Who reads it |
|---|---|---|---|
| Source DXF | binary file, never changed | 1–30 MB | the converter, auditors |
| IFC model (IFC4 + IFC2X3) | text file, one per version | 1–20 MB | Revit, Archicad, Solibri, the viewer |
| `plan.json`: walls, rooms, openings, stairs... with coordinates, confidence, source layer | structured, ~1 000 elements | 0.1–2 MB | the converter, QA, dashboards |
| Review items and engineers' verdicts (Correct / Wrong / Not sure + comment) | small rows, keyed by IFC GlobalId | KB | the team, retraining the role rules |
| Versions: which DXF, which code version, which settings gave which IFC | small rows | KB | anyone asking "why does this wall look like this?" |
| Topology: which wall bounds which room, which door joins which rooms | graph | derived | clash checks, escape-route and area questions |

The files are big and opaque; the elements and verdicts are small and want to be queried. That split drives
everything below.

## The options

**1. Files in object storage (S3-compatible).** DXF and IFC go into buckets, addressed by content hash, so
the same upload is stored once and every version is kept. Self-hosted MinIO or Garage keeps the data in
Luxembourg; any EU cloud S3 works too. Cheap and simple, but it cannot answer "all fire doors under 90 cm
on floor 2" without opening every file.

**2. PostgreSQL (+ PostGIS, JSONB) for elements, trust and feedback.** One row per element: IFC GlobalId,
plan id, version, class, footprint as a PostGIS geometry, properties and quantities as JSONB, confidence,
source layer. Verdicts are rows pointing at a GlobalId, so feedback survives re-conversion as long as the
GlobalId stays (the converter already derives them from the source handle). PostGIS answers spatial
questions directly (overlaps, areas, "what is inside this room"). Mapping the whole IFC schema to tables is
known to be lossless but slow and heavy (the buildingSMART ifcSQL thread, and earlier Oracle studies), so
we would store only what we query and keep the IFC file as the full record.

**3. A graph database (Neo4j) or RDF (ifcOWL).** IFC is a graph: walls bound spaces, openings void walls,
doors fill openings. The TU Dresden work turns IFC into a Neo4j property graph (a meta-graph of the schema
and an object graph of the instances) and compares versions as graph differences. RDF / ifcOWL gives the
same with standard ontologies and SPARQL, at the cost of very large triple counts. Strong for topology
questions; weak as the only store for files and plain properties.

**4. Hybrid relational + graph.** Recent work pairs a relational store for properties with a graph for
topology and lets an LLM pick the right one per question (IfcLLM, arXiv 2605.13236). The Passau
dissertation (M. Schmid) goes the other way and stores the graph inside a relational database as JSON
adjacency lists, which keeps one system to run. For our size (thousands of elements per plan, not
millions) the second is enough: PostgreSQL with a `relations` table or JSONB adjacency, and a graph engine
only if topology queries become the main use.

**5. Speckle, self-hosted, as the data hub.** Open-source (Apache 2.0) server built on Node.js with
PostgreSQL for objects and metadata, S3-compatible storage for files, and Redis; it runs with Docker
Compose (about 4 GB RAM and 20 GB disk minimum). It brings versioning, a web 3D viewer, comments, and
connectors to Revit, Archicad, Rhino and Power BI. We would push each converted model as a new version
and keep our own verdict table next to it. The price: another service to back up and upgrade, and
Speckle's object model sits between us and plain IFC.

**6. BIMserver.** Open-source IFC model server with its own object store and query language. Mature but
little recent activity and few published benchmarks; we would not start a new system on it.

**7. IFC5 / IFCX (future).** buildingSMART is designing IFC5 with a JSON serialisation inspired by USD
layers: a model is a stack of layers, each adding or overriding attributes. That fits our case well (a
"converted from DXF" layer, then an "engineer corrections" layer on top), but it is alpha and not final.
We would export to it once it is stable, not store in it now.

## Recommendation

Start with **object storage + PostgreSQL/PostGIS**; add Speckle when people outside the team need to see
and comment on models; add a graph engine only if topology queries dominate.

```
DXF upload ──► S3 bucket (content hash, never overwritten)
                  │
              converter run  (code version + settings recorded)
                  │
     ┌────────────┴─────────────┐
IFC files ──► S3            elements, trust, rooms ──► PostgreSQL + PostGIS (JSONB)
                                 ▲
     engineers' verdicts ────────┘   keyed by IFC GlobalId, kept across versions
                  │
         optional: push version to self-hosted Speckle for viewing and comments
```

Why this order:

- **Two boring, well-known systems.** Both run in Docker on one small server and both have managed EU
  offerings if the team does not want to run them.
- **Versioning for free.** Files are never overwritten (content hash); every element row carries its
  plan version, so any IFC can be traced back to the DXF, code version and settings that made it.
- **Feedback stays useful.** Verdicts attach to stable GlobalIds, so the "Wrong" marks from one round
  still point at the same wall after a re-conversion, and they become labelled data for the role rules.
- **GDPR and residency.** Plans can name owners and addresses. Self-hosting in Luxembourg (or an EU region),
  encryption at rest, per-project access and a delete-by-project path keep that under control. No plan
  goes to a third-party host without SECO's agreement.

## Element identification (also asked)

The converter already works rules first. Geometry is extracted purely procedurally; layer roles come
from name rules, then from geometric evidence (parallel pairs at wall spacing, door arcs, hatch patterns);
an LLM is asked only about layers still unplaced after both, it can never override a rule, and it is off
by default (zero LLM calls in the demo runs). A learned model would sit in that same last slot.

## Sources

- Speckle server architecture and self-hosting: https://docs.speckle.systems/developers/server/architecture,
  https://docs.speckle.systems/developers/server/self-hosted-vs-cloud-hosted-speckle
- IFC in relational databases (ifcSQL): https://forums.buildingsmart.org/t/ifc-for-relational-databases-ifcsql/1524
- M. Schmid, graph data in relational databases, Univ. Passau dissertation:
  https://opus4.kobv.de/opus4-uni-passau/frontdoor/index/index/docId/1035
- TU Dresden, IFC to Neo4j property graphs (master's thesis and follow-up papers)
- IfcLLM, hybrid relational + graph querying of IFC: https://arxiv.org/abs/2605.13236
- IFC5 development: https://github.com/buildingSMART/IFC5-development,
  https://forums.buildingsmart.org/t/clarifying-the-role-of-ifc-step-spf-in-ifc5-and-its-relationship-with-modern-formats-like-json/6046
