# Stress corpus

10,000 small modelling tasks, each one a plain sentence with real millimetre sizes. Build them in SolidCraft and/or Fusion, convert with Dutchman, compare the results, and file every failure where the bug lives. The goal is to find and fix bugs.

The tasks come from what a modelling kernel has to handle, not from a list of objects: an operation, on a type of surface, in a tricky topology situation, at an unusual scale, in a particular order. A few items carry an everyday object name ("Like a ring: ...") as flavour only.

Example (`S0263 · fillet (variable) × torus × tangent edges × tiny × before shell`):

> Like a ring: model the upper half (Z ≥ 0) of a torus about the Z axis: centre-circle Ø3.6 mm, tube Ø1.2 mm, cut by the plane Z = 0, so the body has a flat annular face at Z = 0. (1) Add a Ø0.14 mm boss, 0.14 mm tall, on the flat cut face with its centre 0.53 mm from the point on the centre circle of the flat cut face at +X (X = 1.8 mm, Y = 0) toward +X, so its side is exactly tangent to the face's outer edge. (2) Fillet both edge loops (inner and outer circle) of the flat cut face with a variable radius: 0.084 mm at the +X point, rising linearly to 0.168 mm at the opposite point. (3) Hollow the body to 0.072 mm walls leaving a closed internal void (no face removed).

## The axes

Every item is one value from each axis. The first 196 items (`"pairwise": true`) are an all-pairs set: every feasible pair of values from two different axes appears in at least one of them (784 pairs). The other 9,804 are random extra combinations. Pairs that cannot be built (for example a sheet-metal bend on a sphere) are left out.

| Axis | Values |
|---|---|
| operation | extrude, cut, revolve, sweep, loft, shell, fillet (constant), fillet (variable), chamfer, draft, pattern (linear), pattern (circular), pattern (along a path), mirror, boolean join, boolean cut, boolean intersect, thread, sheet-metal flange, sheet-metal bend, split, offset face |
| surface | plane, cylinder, cone, sphere, torus, spline |
| topology | clean, tangent edges, coplanar faces, vertex on an edge, thin wall near tolerance, feature at a seam or pole, self-touching body, multiple bodies, edge chain around a corner |
| scale | tiny (features under 1 mm), normal, huge (over 1 m), extreme ratio (1000:1) |
| order | none, before fillet, after fillet, before shell, after shell, before pattern, after pattern |

To propose a new value (or a new axis), open an issue titled "Axis: <name>" with the value, one example brief with real dimensions, and what it would catch. If it is accepted, the new items are generated as extra batches after the last one; existing IDs never change.

`items.jsonl` has one JSON line per item: `id, operation, surface, topology, scale, order, object, pairwise, brief`. `gen.py` writes it from a fixed seed, so `python3 gen.py` reproduces the exact same file.

## Take part

1. Open the issues labelled `batch`. Each is a batch of 100 items: "Batch 07: S0601–S0700".
2. Claim one by assigning it to yourself. Only collaborators can be assigned, so if you are not one yet, comment "claiming" and a maintainer will assign you.
3. Work through the items and tick each box as you finish it.
4. Close the batch issue when every box is ticked.

## Two ways to build an item

- **SolidCraft:** build the part from the brief in SolidCraft. Note anything it refuses, gets wrong, or cannot do.
- **Fusion, then Dutchman:** build the same part in Fusion, export a STEP file from Fusion, convert the Fusion design with Dutchman, and compare the Dutchman result with Fusion's STEP export.

You can do either or both. Say which in the results.

## Scripts for SolidCraft

`scripts.py` writes the machine-runnable form of each item. It uses `gen.py`'s own numbers, so a script builds what its brief prints, and it leaves `items.jsonl` untouched. Each JSON line has `id`, `script` (a `solidcraft-cli` command script), `unsupported` (why there is no script yet) and `expected`.

- `python3 scripts.py --pairwise` writes `scripts-pairwise.jsonl`, the 196 all-pairs items. That sample is committed. `python3 scripts.py` writes `scripts.jsonl` for every item; generate it locally when you need it.
- Run one with `solidcraft-cli run script.json --param-check strict`. A malformed script fails as a parameter error, so a generator mistake can't pass for a kernel bug.
- `expected.bodies` is the number of solid bodies at the end. `expected.volume_mm3` is exact where every step adds or removes a solid with a closed-form volume (primitives, holes and patterns of holes wholly inside their face, bosses, pads, a moved planar face, a split); otherwise it is `null`.
- `python3 scripts.py --check path/to/solidcraft-cli [N] [--jobs J] [--in FILE]` runs the first N scripts of FILE (`scripts.jsonl` by default, all of them), J at a time (4 by default). It writes `check.jsonl`: per item, `built`, `wrong-result` (bodies or volume differ from `expected`), `failed` (with the first error line) or `hang`.
- Items without a script still need building by hand from the brief. This first version covers the steps that map straight onto SolidCraft commands. It doesn't yet cover spline outlines, sheet metal, sweeps, lofts, revolves, variable fillets, patterns along a path, rotated pads, or a fillet or chamfer of a loop that an earlier step splits.

## Record results

Add a pull request with `results/<batch>.jsonl` (for example `results/batch-07.jsonl`). One line per item and pipeline:

```json
{"id": "S0601", "pipeline": "solidcraft", "outcome": "built", "link": ""}
{"id": "S0602", "pipeline": "fusion-dutchman", "outcome": "failed", "link": "https://github.com/dutchman-cad/dutchman/issues/123"}
```

- `pipeline`: `solidcraft` or `fusion-dutchman`
- `outcome`: `built`, `failed`, or `skipped`
- `link`: the issue or PR for a failure, empty otherwise

## File failures

File every failure as its own issue in the repo where the bug lives, and put the item ID (for example `S0602`) in the title:

- SolidCraft could not build it or built it wrong: `tangency-labs/solidcraft`
- Dutchman converted it wrongly: `dutchman-cad/dutchman`
- The file reader or writer is at fault: `cadmpeg/cadmpeg`

Before you file, search that repo for an existing issue about the same problem (search the symptom and the operation name). If one exists, comment on it with the item ID instead of opening a new one.

## Fusion files stay private

Never upload Fusion designs, exports, or screenshots of them, here or anywhere public. SolidCraft's rule is that nothing is taken from commercial CAD. If a failure needs a repro for SolidCraft, rebuild it from scratch in SolidCraft as a small synthetic file and attach that.

## Licence

Apache-2.0, see `LICENSE`.
