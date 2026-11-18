# Stress corpus

Results at a glance: https://dutchman-cad.github.io/stress-corpus/ (every task as a pass, fail or not-started cell, with the fail rate per batch and per axis).

10,000 small modelling tasks, each one a plain sentence with real millimetre sizes. Build them in SolidCraft and/or Fusion, convert with Dutchman, compare the results, and record every failure in a results pull request. The goal is to find and fix bugs.

The tasks come from what a modelling kernel has to handle, not from a list of objects: an operation, on a type of surface, in a tricky topology situation, at a given distance from the exact contact, at an unusual scale, in a particular order, and placed in an awkward spot. A few items carry an everyday object name ("Like a ring: ...") as flavour only.

Example (`S0253 · split × cylinder × tangent edges (gap -1e-6) × normal × after pattern × far`):

> Like a bollard: model a Ø40 × 60 mm cylinder with a vertical axis through the origin, resting on Z = 0. (1) Cut a Ø4 mm hole, 16 mm deep, in the top face 16 mm from the centre of the top face toward -X, then repeat it in a linear pattern of 3 along Y at 6 mm pitch, so the holes are at Y = -6, 0 and +6 mm. (2) Add a Ø4.8 mm boss, 4.8 mm tall, on the top face with its centre 17.600001 mm from the centre of the top face toward +X, so its side overhangs the face's outer edge by 0.000001 mm. (3) Split the body with the plane Z = 30 mm into two bodies and keep both. Finally, move everything 10000 mm along +X: the original origin lands at (10000, 0, 0).

Its `expected` is `{"volume_mm3": 74881.8967, "bodies": 2, "refusal_ok": true}`.

## The axes

Every item is one value from each of seven axes.

| Axis | Values |
|---|---|
| operation | extrude, cut, revolve, sweep, loft, shell, fillet (constant), fillet (variable), chamfer, draft, pattern (linear), pattern (circular), pattern (along a path), mirror, boolean join, boolean cut, boolean intersect, thread, sheet-metal flange, sheet-metal bend, split, offset face |
| surface | plane, cylinder, cone, sphere, torus, spline |
| topology | clean, tangent edges, coplanar faces, vertex on an edge, thin wall near tolerance, feature at a seam or pole, self-touching body, multiple bodies, edge chain around a corner |
| gap | exact, +1e-7, +1e-6, +tol, +2·tol, +1e-4, -1e-6, -tol (only for the six contact topologies below); `none` for the other topologies |
| scale | tiny (features under 1 mm), normal, huge (over 1 m), extreme ratio (1000:1) |
| order | none, before fillet, after fillet, before shell, after shell, before pattern, after pattern |
| placement | origin, rotated, far, rotated + far |

**Gap.** Applies to tangent edges, coplanar faces, vertex on an edge, thin wall near tolerance, feature at a seam or pole, and self-touching body. It says how far from the exact contact the feature sits, in millimetres and absolute (the kernel's tolerance is absolute, 1e-5 mm, so the gaps do not scale with the part): `exact` is 0, `+1e-7` is 0.0000001 mm, `+tol` is 0.00001 mm, `+2·tol` is 0.00002 mm, `+1e-4` is 0.0001 mm, and the two negative values overlap the contact by 0.000001 mm and 0.00001 mm. A positive gap means clear of the contact, a negative gap means overlapping it. What the contact is, per topology:

- tangent edges: how far the boss's side is inside (or, negative, beyond) the face's outer edge;
- coplanar faces: how much taller (or, negative, shorter) the second pad is, so its top is that far from coplanar;
- vertex on an edge: how far the pad's corner is inside (or, negative, beyond) the outer edge;
- thin wall near tolerance: the wall left between the hole and the outer edge (zero or negative means the hole touches or breaks through the edge);
- feature at a seam or pole: how far the notch's nearest side is from the seam line (negative means it overlaps the seam);
- self-touching body: the gap between the two cylinders (negative means they overlap).

**Placement.** The last step of the brief places the whole finished part. `rotated` turns it 17° about X, then 31° about Z, both through the origin (right-hand rule). `far` moves it 10000 mm (10 m) along +X. `rotated + far` rotates first, then moves. The brief gives the exact coordinates: where the original origin lands and where the original X and Z axes point.

To propose a new value (or a new axis), open an issue titled "Axis: <name>" with the value, one example brief with real dimensions, and what it would catch. If it is accepted, the new items are generated as extra batches after the last one; from then on existing IDs never change.

## Coverage

`gen.py` builds the list in three steps, all from one fixed seed:

1. **Three-wise.** Every feasible combination of operation × topology × order appears in at least one item: 1,232 items, spread through every batch (`"coverage": "threewise"`).
2. **Pairwise.** Every feasible pair of values from two different axes, over all seven axes including gap and placement, appears in at least one item: 1,414 feasible pairs. The three-wise items already cover all of them, so this step added 0 more items (`"coverage": "pairwise"` is kept for items it would add).
3. **Random fill.** 8,768 more random combinations bring the total to 10,000 (`"coverage": "random"`). The fill is weighted so each topology is about as likely as any other, and so about 60% of the fill is at the origin.

Combinations that cannot be built (for example a sheet-metal bend on a sphere) are left out.

## Item format

`items.jsonl` has one JSON line per item: `id, operation, surface, topology, gap, scale, order, placement, object, coverage, pairwise, brief, expected`. `pairwise` is true for every item in the three-wise or pairwise sets. `gen.py` writes it from a fixed seed, so `python3 gen.py` reproduces the exact same file, and `python3 gen.py batch 7` prints the body of the Batch 07 issue.

`expected` is what the generator can work out itself, from the same numbers that are in the brief:

```json
"expected": {"volume_mm3": 199998.817, "bodies": 1, "refusal_ok": false}
```

- `volume_mm3`: the total volume of all bodies, only when it is closed form for that exact item (1,070 items so far). If the field is missing, no value is claimed.
- `bodies`: how many separate bodies the finished part has.
- `refusal_ok`: true when a wall or gap in the item is under the kernel tolerance of 1e-5 mm, so refusing to build it is acceptable.

### Scripts for SolidCraft

`scripts.py` gives an item a runnable `solidcraft-cli` command script. It uses `gen.py`'s own numbers, so the script builds what the brief prints, gap and placement included. It never changes `items.jsonl`. Each line holds `id`, `script` (or `null`), `unsupported` (why there is no script yet) and the item's `expected`.

- `python3 scripts.py --batch 7` writes `scripts/batch-07.jsonl`; `python3 scripts.py` writes `scripts.jsonl` for every item. `scripts/batch-01.jsonl` is committed as an example.
- `python3 scripts.py --check path/to/solidcraft-cli scripts/batch-07.jsonl [--jobs J] [--results results/batch-07.jsonl]` runs each script with `--param-check strict`, J at a time. It writes `check.jsonl` with one of the outcomes below and the detail per item, and with `--results`, the batch's results file, with the `signature`, `measured` and `build` fields that `tools/check_results.py` checks. An item without a script comes out `skipped`. Without a closed-form `volume_mm3`, only the body count is checked, and the detail says so.
- This version covers the steps that map straight onto SolidCraft commands: 9,777 of the 10,000 items. It doesn't yet cover a flange along part of a spline sheet's edge, a blend after a sheet is bent, a blend of a loop whose edges are under the tolerance (a rotated pad's corner just past the edge), or a variable fillet of a torus loop that a seam notch splits. Build those by hand from the brief. A pattern along a path is read as N holes in all for "repeat it N times" (as the circular pattern's expected volume counts them), along an arc that leaves C toward +X. On a spline outline, the seam notch's distance along the edge and the bounding-box centre come from the spline as SolidCraft builds it: a periodic cubic through the points, parameterised by chord length.

### FreeCAD through Dutchman

`freecad.py` imports `gen` and reuses `scripts.py`'s numeric step specification to
build the same items as standalone FreeCAD Python macros. Each supported macro
creates PartDesign Bodies with Sketcher constraints and real feature history,
saves `<id>.FCStd`, and exports the final bodies to `<id>.step` in its working
directory. It does not substitute a final BREP for the feature tree. This version
uses no Part workbench solid primitives; future fallbacks must carry
`"history": "partial"` in the generated line.

```sh
python3 freecad.py --batch 1
python3 freecad.py --run freecad/batch-01.jsonl --freecad /path/to/FreeCADCmd --dutchman /path/to/dutchman --cli /path/to/solidcraft-cli --jobs 3 --timeout 600 --results /path/to/batch01-results.jsonl
```

For a FreeCAD app installed in `/Applications` on macOS (with `dutchman` on PATH):

```sh
python3 freecad.py --batch 1
python3 freecad.py --run freecad/batch-01.jsonl --freecad /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd --dutchman dutchman --cli ~/.local/bin/solidcraft-cli --jobs 3 --timeout 600 --results ~/builds/fc-corpus/batch01-results.jsonl
```

Without `--batch`, generation writes all 10,000 records to `freecad.jsonl`.
Batch generation writes `freecad/batch-NN.jsonl`; every record has `id`, `macro`
(or null), `unsupported` (or null), and the original `expected` object. An
unsupported reason names the first step without an implemented mapping. The
current generator emits macros for 5,213 items and explicit unsupported records
for 4,787 (chiefly 2,513 closed-shell steps, 488 splits, and 472 face offsets). Current
limits include closed shells without removed faces, variable fillets, path
patterns, modeled threads, split bodies, offset faces, sheet metal, and polar
patterns about an offset axis. Additional limitations inherited from `scripts.py`
are reported explicitly. Unsupported items are `skipped`, never refusals.

The runner builds each macro in its own temporary directory, checks FreeCAD's
final volume/body count against `expected`, converts the FCStd with
`dutchman convert INPUT.FCStd OUTPUT.sc --json`, and measures the `.sc` using
`solidcraft-cli run --param-check strict`. FreeCAD build errors, invalid shapes,
source expectation mismatches, and source timeouts are `source-failed`; they are
not attributed to Dutchman or SolidCraft. Downstream timeouts are `hang` and
signal exits/panics are `crash`. Other downstream errors use `refusal_ok`.
`--timeout` bounds each of the three stages (600 seconds by default), and kills
its entire process group on timeout. The runner is intended for Linux/macOS.

Results use pipeline `freecad-dutchman`, retain all three releases in `version`
and `versions`, and include `source_measured` and Dutchman's `history` counts
(`mapped` / `total`, null if no summary was returned). Conversion diagnostics
are retained under `conversion`. Measurements use the same summary parser as
`scripts.py` and the result validator's relative volume tolerance of 1e-6.
Where no closed-form volume exists, `correct` checks the body count only and
`detail` states that geometry was not inspected. Results are flushed as items
finish and sorted by ID on completion. Generated macros, documents, and local
reports need not be committed.

For an extracted Linux AppImage, use a launcher that sets `PYTHONHOME` to
`<extraction>/usr` and `LD_LIBRARY_PATH` to `<extraction>/usr/lib`, then executes
`<extraction>/usr/bin/freecadcmd`. This uses only a user-level download. Set
`FREECAD_CMD` to this launcher when running
`python3 -m unittest discover -s tests` to include real headless API smoke tests.

Validated on Linux with FreeCAD 1.1.4, Dutchman 0.2.10, and solidcraft-cli
0.44.1. Batch 01 with three workers: 2 correct, 37 wrong-result, 1 wrong-refusal,
20 source-failed, and 40 skipped; the result validator accepts all 100 lines.
The external run report is not committed. All 10,000 generator records were
classified, and every supported macro passed Python compilation.

Clean-room API references: [Sketcher scripting](https://reqrefusion.github.io/FreeCAD-Documentation-html/wiki/Sketcher_scripting.html),
[Pad](https://reqrefusion.github.io/FreeCAD-Documentation-html/wiki/PartDesign_Pad.html),
[Revolution](https://reqrefusion.github.io/FreeCAD-Documentation-html/wiki/PartDesign_Revolution.html),
[Thickness](https://reqrefusion.github.io/FreeCAD-Documentation-html/wiki/PartDesign_Thickness.html),
[Boolean](https://reqrefusion.github.io/FreeCAD-Documentation-html/wiki/PartDesign_Boolean.html),
[LinearPattern](https://reqrefusion.github.io/FreeCAD-Documentation-html/wiki/PartDesign_LinearPattern.html),
and [AdditivePipe](https://reqrefusion.github.io/FreeCAD-Documentation-html/wiki/PartDesign_AdditivePipe.html).
The macros were written from documentation and runtime API inspection, without
reading or downloading FreeCAD source code.

## Take part

1. Open the issues labelled `batch`. Each is a batch of 100 items: "Batch 07: S0601–S0700".
2. Claim one by commenting `/claim` on it (or "claiming"). A bot assigns you at once; no repo access is needed. Hold at most two open batches at a time. Comment `/unclaim` to give a batch back.
3. Work through the items and open a results PR. When its checks pass it merges by itself, and a bot ticks every item that has a result.
4. A maintainer closes the batch when every item has a result.

## Two ways to build an item

- **SolidCraft:** build the part from the brief in SolidCraft. Note anything it refuses, gets wrong, or cannot do.
- **Fusion, then Dutchman:** build the same part in Fusion, export a STEP file from Fusion, convert the Fusion design with Dutchman, and compare the Dutchman result with Fusion's STEP export. Compare volume, surface area, face, edge and vertex counts, and a distance bound between the two shapes.

You can do either or both. Say which in the results.

## Outcomes

| Outcome | Meaning |
|---|---|
| `correct` | It built, and the result matches the expected values (or, for Fusion → Dutchman, matches Fusion's STEP). |
| `wrong-result` | It built, but the volume, body count or geometry is wrong. These are the worst bugs: nothing reports an error. |
| `correct-refusal` | It was refused, and `refusal_ok` is true. |
| `wrong-refusal` | It was refused, but `refusal_ok` is false: the item is valid. |
| `crash` | The program crashed or panicked. |
| `hang` | It did not finish within 10 minutes. Stop it, record the step it stuck on as the signature, and go on to the next item. |
| `skipped` | Not attempted. |
| `source-failed` | FreeCAD could not build the source document correctly (FreeCAD route only). |

Items with no `volume_mm3` cannot be called `correct` from the number alone: check the body count and look at the part, and say what you checked.

## Record results

Add a pull request with `results/<batch>.jsonl` (for example `results/batch-07.jsonl`). One line per item and pipeline:

```json
{"id": "S0601", "pipeline": "solidcraft", "outcome": "correct", "version": "solidcraft 0.123.0", "link": ""}
{"id": "S0602", "pipeline": "fusion-dutchman", "outcome": "wrong-result", "version": "dutchman 0.45.0", "link": "https://github.com/dutchman-cad/dutchman/issues/123"}
```

- `pipeline`: `solidcraft`, `fusion-dutchman`, or `freecad-dutchman`
- `outcome`: one of the outcomes above
- `version`: the SolidCraft release or commit, or the Dutchman version, so a result can be rerun on a newer build
- `link`: an existing issue or PR for the failure, empty otherwise
- `signature`, `measured`, `build`: see Failures below
- `rerun_of` (optional, a string): the earlier build this line checks again, for example `"solidcraft 0.43.0"`. See Check again on a newer build below.

**Checks on a results PR.** CI refuses a results PR that changes anything except `results/batch-NN.jsonl`, adds design files, exports or images, comes from someone who hasn't claimed that batch, lists items outside the batch, or has lines that fail `tools/check_results.py`. Each error names the file and line.

## Check again on a newer build

When a newer build may have fixed a failure, run the failed items again and record the new result with `rerun_of` set to the `version` (or `build`) of the result you are re-checking:

```json
{"id": "S0602", "pipeline": "solidcraft", "outcome": "correct", "version": "solidcraft 0.44.0", "rerun_of": "solidcraft 0.43.0", "link": ""}
```

The results page shows a "Fixed since" section: items that failed on the earlier build and pass on the newer one, grouped by cause or issue. A line with `rerun_of` replaces the earlier result in the grid and totals. Keep the earlier line as it is.

## Failures

Do not file bugs upstream yourself. Put every failure in your results pull request, with its `signature`. Maintainers group failures by cause and file one issue per cause, so you do not need to search other repos or shrink items.

Add these fields to a failing line (`correct` lines may carry `measured` and `build` too):

```json
{"id": "S0602", "pipeline": "fusion-dutchman", "outcome": "wrong-result", "version": "dutchman 0.45.0", "build": "0.45.0", "link": "", "signature": "volume 3% low after fillet @ history replay", "measured": {"volume_mm3": 1234.5, "bodies": 1, "faces": 9, "edges": 20, "vertices": 12}}
```

- `signature`: the first error or refusal message exactly as the program shows it, then ` @ ` and the failing operation or command. For a wrong result with no message, say in a few words what is wrong. Required for every outcome except `correct`.
- `measured`: what you measured on the result. `volume_mm3` and `bodies` are compared with `expected`; faces, edges and vertices are optional.
- `build`: the SolidCraft or Dutchman version.
- `link` stays empty unless an issue already exists.

A check runs on every results pull request (`python3 tools/check_results.py`). It rejects unknown IDs, outcomes or pipelines, a missing signature, a duplicate line, a `correct` whose volume or body count differs from `expected` (volume tolerance 1e-6 relative), and a `wrong-result` whose measured volume and body count match. Run it before you push.

Maintainers run `python3 tools/cluster.py --known known.json`. It groups failures by pipeline and normalised signature (numbers, ids and paths removed, and coordinates such as `(x, y, z)` written as `<point>`, so an edge named by its position doesn't split one cause into a group per position), picks the smallest item in each group, and prints an issue draft: the brief, every item ID in the group, the axis values the group shares, and counts by version. After filing, the issue URL is added to `known.json` under the cluster's key, and that cluster is skipped from then on.

If something looks odd and does not fit a results line, open an issue here and describe it. Do not file `correct-refusal` results as bugs: they are expected.

## Fusion files stay private

Never upload Fusion designs, exports, or screenshots of them, here or anywhere public. SolidCraft's rule is that nothing is taken from commercial CAD. If a failure needs a repro for SolidCraft, rebuild it from scratch in SolidCraft as a small synthetic file and attach that.

## Licence

Apache-2.0, see `LICENSE`.
