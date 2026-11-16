#!/usr/bin/env python3
"""Machine-runnable form of the stress corpus: a SolidCraft command script per item.

Reads the same axes and numbers as gen.py (it imports it), so each script builds exactly what
its brief says: every number is the one the brief prints (gen.fmt / gen.ex), never recomputed
differently. Writes scripts.jsonl, one JSON object per line:

  {"id": "S0001", "script": {"commands": [...]} | null, "unsupported": "<why>" | null,
   "expected": {"bodies": n, "volume_mm3": v | null} | null}

- "script" runs with `solidcraft-cli run script.json --param-check strict`.
- "unsupported" names the step this first version can't express yet (spline outlines, sheet
  metal, sweeps, lofts, revolves, variable fillets, rotated pads, ...); those items stay
  hand-built from their brief.
- "expected.volume_mm3" is given only where every step adds or removes a solid whose volume is
  known in closed form (primitives, holes, bosses, pads, patterns of holes, a moved planar
  face, a split); otherwise null. "expected.bodies" is the number of solid bodies at the end.

items.jsonl is not touched: `python3 gen.py` still reproduces it byte for byte.

  python3 scripts.py                      # writes scripts.jsonl (every item)
  python3 scripts.py --pairwise           # writes scripts-pairwise.jsonl (the 196 all-pairs
                                          # items only: the committed sample)
  python3 scripts.py --check CLI [N] [--jobs J] [--in FILE]
                                          # runs the first N scripts of FILE (scripts.jsonl;
                                          # all by default) through solidcraft-cli, J at a time
                                          # (4), and writes check.jsonl: the outcome per item
"""
import json
import math
import os
import subprocess
import sys
import tempfile

import gen

PI = math.pi
PART = "Part"


class Unsupported(Exception):
    pass


def F(s):
    """A number as the brief prints it."""
    return float(s)


def f3(x):
    return F(gen.fmt(x))


class Item:
    """The script and the running closed-form volume of one item."""

    def __init__(self):
        self.cmds = []
        self.volume = 0.0   # None once a step's volume is not known in closed form
        self.bodies = 1
        self.names = 0

    def add(self, command, **params):
        self.cmds.append({"command": command, "params": params})

    def name(self, stem):
        self.names += 1
        return "%s%d" % (stem, self.names)

    def vol(self, dv):
        if self.volume is not None:
            self.volume += dv

    def unknown_volume(self):
        self.volume = None


# ------------------------------------------------------------------ the body
def body(it, su, sc):
    """The base body. Returns the frame: face plane z, the outward direction o (+1 up, -1 down),
    the centre C (x, y) of the face, hu, H, the face's outer loop pick points."""
    if su == "spline":
        raise Unsupported("spline outline (needs a sketch spline)")
    b = gen.body(su, sc, False, None)
    fr = {"o": 1.0, "cx": 0.0, "cy": 0.0, "hu": b["hu"], "H": b["H"], "Wmin": b["Wmin"], "z0": b["z0"]}
    if su == "plane":
        A, B, H = {"tiny": (4, 3, 2), "normal": (60, 40, 25), "huge": (2400, 1600, 1000),
                   "extreme ratio": (1000, 200, 1)}[sc]
        A, B, H = f3(A), f3(B), f3(H)
        it.add("solid.box", length=A, width=B, height=H, corner=[-A / 2, -B / 2, 0.0],
               operation="new", body_name=PART)
        it.vol(A * B * H)
        fr.update(fz=H, loop=[[0.0, -B / 2, H], [A / 2, 0.0, H], [0.0, B / 2, H], [-A / 2, 0.0, H]],
                  ext0=A / 2, sides=[[A / 2, 0.0, H / 2], [-A / 2, 0.0, H / 2], [0.0, B / 2, H / 2], [0.0, -B / 2, H / 2]],
                  edge_py=[0.0, B / 2], kind="block", A=A, B=B, bbox_c=[0.0, 0.0, H / 2])
    elif su == "cylinder":
        D, H = {"tiny": (3, 4), "normal": (40, 60), "huge": (1200, 2400), "extreme ratio": (1, 1000)}[sc]
        D, H = f3(D), f3(H)
        it.add("solid.cylinder", radius=D / 2, height=H, base=[0.0, 0.0, 0.0], axis="Z",
               operation="new", body_name=PART)
        it.vol(PI * (D / 2) ** 2 * H)
        fr.update(fz=H, loop=[[0.0, -D / 2, H]], ext0=D / 2, sides=[[0.0, -D / 2, H / 2]],
                  edge_py=[0.0, D / 2], kind="round", R=D / 2, bbox_c=[0.0, 0.0, H / 2])
    elif su == "cone":
        D1, D2, H = {"tiny": (4, 2, 3), "normal": (50, 20, 50), "huge": (2000, 800, 2000),
                     "extreme ratio": (6, 1, 1000)}[sc]
        D1, D2, H = f3(D1), f3(D2), f3(H)
        it.add("solid.cone", radius=D1 / 2, top_radius=D2 / 2, height=H, base=[0.0, 0.0, 0.0],
               operation="new", body_name=PART)
        r1, r2 = D1 / 2, D2 / 2
        it.vol(PI * H / 3 * (r1 * r1 + r1 * r2 + r2 * r2))
        fr.update(fz=H, loop=[[0.0, -r2, H]], ext0=r1, sides=[[0.0, -(r1 + r2) / 2, H / 2]],
                  kind="round", R=r2, bbox_c=[0.0, 0.0, H / 2])
    elif su == "sphere":
        D = f3({"tiny": 4, "normal": 50, "huge": 2400, "extreme ratio": 1000}[sc])
        R = D / 2
        if sc == "extreme ratio":
            k = F(("%.7f" % math.sqrt(R * R - 0.25)).rstrip("0"))
        else:
            k = f3(0.6 * R)
        it.add("solid.sphere", center=[0.0, 0.0, 0.0], radius=R, operation="new", body_name=PART)
        it.add("solid.box", length=2 * R + 2, width=2 * R + 2, height=R, corner=[-R - 1, -R - 1, k],
               operation="cut", targets=[PART])
        h = R - k
        it.vol(4.0 / 3 * PI * R ** 3 - PI * h * h * (3 * R - h) / 3)
        rf = math.sqrt(R * R - k * k)
        fr.update(fz=k, loop=[[0.0, -rf, k]], ext0=R, kind="round", R=rf,
                  bbox_c=[0.0, 0.0, (k - R) / 2], sides=None)
    elif su == "torus":
        Dm, Dt = {"tiny": (3.6, 1.2), "normal": (60, 16), "huge": (3000, 800), "extreme ratio": (1000, 1)}[sc]
        Dm, Dt = f3(Dm), f3(Dt)
        Rm, r = Dm / 2, Dt / 2
        it.add("solid.torus", center=[0.0, 0.0, 0.0], major=Rm, minor=r, operation="new", body_name=PART)
        big = Rm + r + 1
        it.add("solid.box", length=2 * big, width=2 * big, height=r + 1, corner=[-big, -big, -(r + 1)],
               operation="cut", targets=[PART])
        it.vol(PI * PI * Rm * r * r)
        # the flat face is at Z = 0 and the body lies above it: features on it point down
        fr.update(fz=0.0, o=-1.0, cx=Rm, loop=[[0.0, -(Rm - r), 0.0], [0.0, -(Rm + r), 0.0]],
                  ext0=Rm + r, kind="annulus", Ri=Rm - r, Ro=Rm + r, bbox_c=[0.0, 0.0, r / 2], sides=None)
    return fr


def cyl(it, r, h, x, y, z_from, up, op, name=None, body_name=None):
    """A vertical cylinder of radius r and height h starting at z_from, going +Z if up else -Z."""
    base = [x, y, z_from if up else z_from - h]
    p = {"radius": r, "height": h, "base": base, "axis": "Z", "operation": op}
    if op != "new":
        p["targets"] = [PART]
    if name:
        p["name"] = name
    if body_name:
        p["body_name"] = body_name
    it.cmds.append({"command": "solid.cylinder", "params": p})


def outward(fr):
    return fr["o"] > 0


# ------------------------------------------------------------------ the steps
def prelude(it, to, su, sc, fr, e):
    if to == "clean":
        return
    s, h = f3(gen.sig(0.12 * e)), f3(gen.sig(0.12 * e))
    u0 = F(gen.ex(gen.inset(fr["hu"], e)))
    dd = f3(0.5 * min(fr["H"], e))
    cx, cy, fz, out = fr["cx"], fr["cy"], fr["fz"], outward(fr)
    if to == "tangent edges":
        cyl(it, s / 2, h, cx + F(gen.ex(fr["hu"] - s / 2.0)), cy, fz, out, "join")
        it.vol(PI * (s / 2) ** 2 * h)
    elif to == "coplanar faces":
        z = fz if out else fz - h
        for dy in (-s / 2, s / 2):
            it.add("solid.box", length=s, width=s, height=h, corner=[cx + u0 - s / 2, cy + dy, z],
                   operation="join", targets=[PART])
        it.vol(2 * s * s * h)
    elif to == "vertex on an edge":
        raise Unsupported("rotated pad (solid.box has no rotation)")
    elif to == "thin wall near tolerance":
        tw = gen.THIN[sc]
        cyl(it, s / 2, dd, cx + F(gen.ex(fr["hu"] - tw - s / 2.0)), cy, fz, not out, "cut")
        it.vol(-PI * (s / 2) ** 2 * dd)
    elif to == "feature at a seam or pole":
        # a notch straddling the outer edge: only part of it meets the material
        cyl(it, s / 2, dd, cx + fr["hu"], cy, fz, not out, "cut")
        if fr["kind"] == "round" and su == "cylinder":
            it.unknown_volume()  # a disc centred on a circle: lens-shaped overlap
        else:
            it.unknown_volume()
    elif to == "self-touching body":
        for dy in (-s / 2, s / 2):
            cyl(it, s / 2, h, cx + u0, cy + dy, fz, out, "join")
        it.vol(2 * PI * (s / 2) ** 2 * h)
    elif to == "multiple bodies":
        gap = f3(gen.sig(0.1 * e))
        cyl(it, s / 2, h, fr["ext0"] + gap + s / 2, 0.0, 0.0, True, "new", body_name="Extra")
        it.vol(PI * (s / 2) ** 2 * h)
        it.bodies += 1
    elif to == "edge chain around a corner":
        if su not in ("plane", "cylinder"):
            raise Unsupported("edge chain on " + su)
        ey = fr["edge_py"][1]
        z = fz - dd if out else fz
        it.add("solid.box", length=s, width=s, height=dd, corner=[-s / 2, ey - s / 2, z],
               operation="cut", targets=[PART])
        if su == "plane":
            it.vol(-s * (s / 2) * dd)
        else:
            it.unknown_volume()


def inside_face(fr, x, y, r):
    """Whether a hole of radius r at (x, y) lies wholly inside the face (its volume is then known)."""
    if fr["kind"] == "block":
        return abs(x) + r <= fr["A"] / 2 and abs(y) + r <= fr["B"] / 2
    if fr["kind"] == "round":
        return math.hypot(x, y) + r <= fr["R"]
    if fr["kind"] == "annulus":
        rho = math.hypot(x, y)
        return fr["Ri"] + r <= rho and rho + r <= fr["Ro"]
    return False


def holes(it, fr, d, dep, centres):
    """The volume of holes of diameter d and depth dep at the centres, when all lie inside the face."""
    if all(inside_face(fr, x, y, d / 2) for x, y in centres):
        it.vol(-len(centres) * PI * (d / 2) ** 2 * dep)
    else:
        it.unknown_volume()


def pattern_holes(it, fr, e, centred):
    """gen.pattern_step: a hole u toward -X from C, repeated 3 times along Y at pitch p."""
    d, p, dep = f3(gen.sig(0.1 * e)), f3(gen.sig(0.15 * e)), f3(gen.sig(0.4 * min(fr["H"], e)))
    u = F(gen.ex(gen.inset(fr["hu"], e)))
    name = it.name("Hole")
    x = fr["cx"] - u
    cyl(it, d / 2, dep, x, fr["cy"], fr["fz"], not outward(fr), "cut", name=name)
    it.add("solid.pattern.rectangular", features=[name], dir1=[0.0, 1.0, 0.0], count1=3, spacing1=p)
    holes(it, fr, d, dep, [(x, fr["cy"] + k * p) for k in range(3)])


def fillet_loop(it, fr, r, to):
    if to not in ("clean", "coplanar faces", "self-touching body", "multiple bodies"):
        raise Unsupported("fillet of a loop that the prelude splits")
    it.add("solid.fillet", edges=fr["loop"], radius=r)
    it.unknown_volume()


def closed_shell(it, w):
    it.add("solid.shell", faces=[], thickness=w, body=PART)
    it.unknown_volume()


def op_step(it, op, su, sc, fr, e, to):
    cx, cy, fz, out = fr["cx"], fr["cy"], fr["fz"], outward(fr)
    d = f3(gen.sig(0.1 * e))
    dep = f3(gen.sig(0.4 * min(fr["H"], e)))
    r = f3(gen.sig(0.07 * e))
    w = f3(gen.sig(0.05 * e))
    c = f3(gen.sig(0.05 * e))
    p = f3(gen.sig(0.15 * e))
    if op == "extrude":
        # the brief says "upward" (+Z) for every face
        cyl(it, d / 2, dep, cx, cy, fz, True, "join")
        if out:
            it.vol(PI * (d / 2) ** 2 * dep)
        else:
            it.unknown_volume()
    elif op == "cut":
        cyl(it, d / 2, dep, cx, cy, fz, not out, "cut")
        holes(it, fr, d, dep, [(cx, cy)])
    elif op == "shell":
        it.add("solid.shell", faces=[[cx, cy, fz]], thickness=w, body=PART)
        it.unknown_volume()
    elif op == "fillet (constant)":
        fillet_loop(it, fr, r, to)
    elif op == "chamfer":
        if to not in ("clean", "coplanar faces", "self-touching body", "multiple bodies"):
            raise Unsupported("chamfer of a loop that the prelude splits")
        it.add("solid.chamfer", edges=fr["loop"], distance=c, distance2=f3(2 * c))
        it.unknown_volume()
    elif op == "draft":
        if not fr.get("sides"):
            raise Unsupported("draft on " + su)
        it.add("solid.draft", faces=fr["sides"], angle="3 deg", neutral="XY")
        it.unknown_volume()
    elif op == "pattern (linear)":
        name = it.name("Hole")
        cyl(it, d / 2, dep, cx, cy - p, fz, not out, "cut", name=name)
        it.add("solid.pattern.rectangular", features=[name], dir1=[0.0, 1.0, 0.0], count1=3, spacing1=p)
        holes(it, fr, d, dep, [(cx, cy + k * p) for k in (-1, 0, 1)])
    elif op == "pattern (circular)":
        if cx != 0.0 or cy != 0.0:
            raise Unsupported("circular pattern about an axis off the origin")
        name = it.name("Hole")
        cyl(it, d / 2, dep, cx + f3(1.5 * d), cy, fz, not out, "cut", name=name)
        it.add("solid.pattern.circular", features=[name], axis="Z", count=6, angle="360 deg")
        rho = f3(1.5 * d)
        holes(it, fr, d, dep, [(rho * math.cos(k * PI / 3), rho * math.sin(k * PI / 3)) for k in range(6)])
    elif op == "mirror":
        name = it.name("Boss")
        z = fz if out else fz - dep
        it.add("solid.box", length=d, width=d, height=dep, corner=[cx - d / 2, cy + f3(1.5 * d) - d / 2, z],
               operation="join", targets=[PART], name=name)
        it.add("solid.mirror", features=[name], plane="XZ", combine=True)
        it.vol(2 * d * d * dep)
    elif op == "boolean join":
        it.add("solid.sphere", center=[cx, cy, fz], radius=d, operation="join", targets=[PART])
        it.unknown_volume()
    elif op == "boolean cut":
        it.add("solid.cylinder", radius=d, height=f3(8 * d), base=[cx, cy - f3(8 * d) / 2, fz - fr["o"] * d / 2],
               axis="Y", operation="cut", targets=[PART])
        it.unknown_volume()
    elif op == "boolean intersect":
        rad = f3(1.2 * math.sqrt(fr["Wmin"] ** 2 + fr["H"] ** 2)) / 2
        it.add("solid.sphere", center=fr["bbox_c"], radius=rad, operation="intersect", targets=[PART])
        it.unknown_volume()
    elif op == "thread":
        n, pi, ln = {"tiny": (1.6, 0.35, 1.6), "normal": (12, 1.75, 14), "huge": (100, 6, 120),
                     "extreme ratio": (1, 0.25, 0.5)}[sc]
        if not out:
            raise Unsupported("thread boss pointing down")
        cyl(it, n / 2, ln, cx, cy, fz, True, "join")
        it.add("solid.chamfer", edges=[[cx, cy - n / 2, fz + ln]], distance=pi)
        it.add("solid.thread", face=[cx, cy - n / 2, fz + ln / 2], designation="M%sx%s" % (gen.fmt(n), gen.fmt(pi)),
               modeled=True)
        it.unknown_volume()
    elif op == "split":
        z = F(gen.fmt(gen.sig(fr["z0"] + 0.5 * fr["H"], 4)))
        it.add("solid.split_body", body=PART, plane={"origin": [0.0, 0.0, z], "normal": [0.0, 0.0, 1.0]})
        it.bodies += 1
    elif op == "offset face":
        it.add("solid.offset_face", faces=[[cx, cy, fz]], distance=dep)
        if fr["kind"] == "block":
            it.vol(fr["A"] * fr["B"] * dep)
        elif su == "cylinder":
            it.vol(PI * fr["R"] ** 2 * dep)
        else:
            it.unknown_volume()
    else:
        raise Unsupported(op)


def script_for(row):
    op, su, to, sc, orr = row
    if op.startswith("sheet-metal"):
        raise Unsupported("sheet metal")
    if op in ("revolve", "sweep", "loft", "fillet (variable)", "pattern (along a path)"):
        raise Unsupported(op + " (needs a sketch)")
    it = Item()
    fr = body(it, su, sc)
    e = min(fr["Wmin"], 4 * fr["H"])
    # the same order as gen.brief_for: before-step, prelude, operation, after-step
    if orr == "after fillet":
        fillet_loop(it, fr, f3(gen.sig(0.05 * e)), "clean")
    elif orr == "after shell":
        closed_shell(it, f3(gen.sig(0.06 * e)))
    elif orr == "after pattern":
        pattern_holes(it, fr, e, False)
    prelude(it, to, su, sc, fr, e)
    op_step(it, op, su, sc, fr, e, to)
    if orr == "before fillet":
        fillet_loop(it, fr, f3(gen.sig(0.05 * e)), to)
    elif orr == "before shell":
        closed_shell(it, f3(gen.sig(0.06 * e)))
    elif orr == "before pattern":
        pattern_holes(it, fr, e, False)
    vol = None if it.volume is None else float("%.12g" % it.volume)
    return {"commands": it.cmds}, {"bodies": it.bodies, "volume_mm3": vol}


def rows_in_order():
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "items.jsonl"), encoding="utf-8"):
        d = json.loads(line)
        yield d["id"], (d["operation"], d["surface"], d["topology"], d["scale"], d["order"]), d["pairwise"]


def main(out="scripts.jsonl", pairwise_only=False):
    n = ok = 0
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for iid, row, pairwise in rows_in_order():
            if pairwise_only and not pairwise:
                continue
            n += 1
            try:
                script, expected = script_for(row)
                rec = {"id": iid, "script": script, "unsupported": None, "expected": expected}
                ok += 1
            except Unsupported as why:
                rec = {"id": iid, "script": None, "unsupported": str(why), "expected": None}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("wrote %d items to %s: %d with a script, %d without" % (n, out, ok, n - ok), file=sys.stderr)


# ------------------------------------------------------------------ check
TIMEOUT_S = 120


def check_one(cli, tmp, r):
    """One item through the CLI: its outcome against `expected`."""
    path = os.path.join(tmp, r["id"] + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(r["script"], f)
    try:
        p = subprocess.run([cli, "run", path, "--param-check", "strict"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"id": r["id"], "outcome": "hang", "detail": "no result in %d s" % TIMEOUT_S}
    if p.returncode != 0:
        return {"id": r["id"], "outcome": "failed", "detail": first_error(p.stderr + p.stdout)}
    got, exp = measured(p.stdout), r["expected"]
    if not got:
        return {"id": r["id"], "outcome": "built", "detail": "no summary to compare"}
    bodies, vol = got
    wrong = bodies != exp["bodies"] or (
        exp["volume_mm3"] is not None and abs(vol - exp["volume_mm3"]) > 1e-6 * max(1.0, exp["volume_mm3"]))
    if wrong:
        return {"id": r["id"], "outcome": "wrong-result",
                "detail": "bodies %s volume %r (expected %s, %r)" % (bodies, vol, exp["bodies"], exp["volume_mm3"])}
    return {"id": r["id"], "outcome": "built", "detail": ""}


def check(cli, limit=None, jobs=4, source="scripts.jsonl", report="check.jsonl"):
    """Run each script through the CLI, `jobs` at a time; compare the bodies and the volume where
    one is expected. Each result is written as it lands; the report ends sorted by item."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    recs = [json.loads(l) for l in open(source, encoding="utf-8")]
    recs = [r for r in recs if r["script"]][:limit]
    counts, results = {}, []
    with open(report, "w", encoding="utf-8", newline="\n") as fh, tempfile.TemporaryDirectory() as tmp, \
            ThreadPoolExecutor(max_workers=jobs) as pool:
        for fut in as_completed([pool.submit(check_one, cli, tmp, r) for r in recs]):
            res = fut.result()
            results.append(res)
            counts[res["outcome"]] = counts.get(res["outcome"], 0) + 1
            fh.write(json.dumps(res) + "\n")
            fh.flush()
    with open(report, "w", encoding="utf-8", newline="\n") as fh:
        for res in sorted(results, key=lambda x: x["id"]):
            fh.write(json.dumps(res) + "\n")
    print("checked %d scripts: %s" % (len(recs), counts), file=sys.stderr)


def first_error(text):
    for line in text.splitlines():
        if "error" in line.lower() or "panick" in line.lower():
            return line.strip()[:300]
    return text.strip().splitlines()[-1][:300] if text.strip() else ""


def measured(stdout):
    """The run's body count and total volume, from its JSON summary."""
    try:
        start = stdout.index("{")
        data = json.loads(stdout[start:])
    except ValueError:
        return None
    total = data.get("total") or {}
    return data.get("body_count"), total.get("volume_mm3")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        args = sys.argv[2:]
        opts = {"--jobs": "4", "--in": "scripts.jsonl"}
        for flag in opts:
            if flag in args:
                i = args.index(flag)
                opts[flag] = args[i + 1]
                del args[i:i + 2]
        check(args[0], int(args[1]) if len(args) > 1 else None, int(opts["--jobs"]), opts["--in"])
    elif len(sys.argv) > 1 and sys.argv[1] == "--pairwise":
        main(sys.argv[2] if len(sys.argv) > 2 else "scripts-pairwise.jsonl", pairwise_only=True)
    else:
        main(*sys.argv[1:2])
