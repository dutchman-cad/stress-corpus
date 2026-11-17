#!/usr/bin/env python3
"""Machine-runnable form of the stress corpus: a SolidCraft command script per item.

It imports gen.py and builds each item from the numbers its brief prints (gen.fmt, gen.ex,
gen.fd), so a script makes what its brief says, gap and placement included. It never touches
items.jsonl. One JSON object per line:

  {"id": "S0601", "script": {"commands": [...]} | null, "unsupported": "<why>" | null,
   "expected": <the item's own expected object>}

- "script" runs with `solidcraft-cli run script.json --param-check strict`, so a malformed
  script fails as a parameter error and a generator mistake can't pass for a kernel bug.
- "unsupported" names the step this version can't express yet (spline outlines, sheet metal,
  sweeps, lofts, revolves, variable fillets, patterns along a path, the rotated pad, ...): build
  those items by hand from the brief.

  python3 scripts.py --batch 7            # scripts/batch-07.jsonl, the items of Batch 07
  python3 scripts.py                      # scripts.jsonl, every item
  python3 scripts.py --check CLI FILE [--jobs J] [--results results/batch-07.jsonl]
      Runs FILE's scripts through solidcraft-cli, J at a time (4), and writes check.jsonl:
      the README's outcome per item (correct, wrong-result, correct-refusal, wrong-refusal,
      crash, hang, skipped) with the detail. --results also writes the batch results file in
      the README's format (pipeline "solidcraft", the CLI's version).
"""
import json
import re
import math
import os
import subprocess
import sys
import tempfile

import gen

PART = "Part"


class Unsupported(Exception):
    pass


def F(s):
    """A number as the brief prints it."""
    return float(s)


def f3(x):
    return F(gen.fmt(x))


class Item:
    def __init__(self):
        self.cmds = []
        self.bodies = [PART]
        self.names = 0

    def add(self, command, **params):
        if params.get("targets") == [PART] and PART + " (1)" in self.bodies:
            # after a split, a feature on the face may land on either piece
            params["targets"] = [PART, PART + " (1)"]
        self.cmds.append({"command": command, "params": params})

    def name(self, stem):
        self.names += 1
        return "%s%d" % (stem, self.names)


# ------------------------------------------------------------------ the body
def body(it, su, sc):
    """The base body, and its frame: the face's plane z and outward direction o (+1 up, -1 down),
    the face centre C, hu, H, the face's outer loop picks (off every seam and feature)."""
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
        fr.update(fz=H, loop=[[0.0, -B / 2, H], [A / 2, 0.0, H], [0.0, B / 2, H], [-A / 2, 0.0, H]],
                  ext0=A / 2, sides=[[A / 2, 0.0, H / 2], [-A / 2, 0.0, H / 2], [0.0, B / 2, H / 2], [0.0, -B / 2, H / 2]],
                  edge_py=B / 2, rim=None, bbox_c=[0.0, 0.0, H / 2])
    elif su == "cylinder":
        D, H = {"tiny": (3, 4), "normal": (40, 60), "huge": (1200, 2400), "extreme ratio": (1, 1000)}[sc]
        D, H = f3(D), f3(H)
        it.add("solid.cylinder", radius=D / 2, height=H, base=[0.0, 0.0, 0.0], axis="Z",
               operation="new", body_name=PART)
        fr.update(fz=H, loop=[[0.0, -D / 2, H]], ext0=D / 2, sides=[[0.0, -D / 2, H / 2]],
                  edge_py=D / 2, rim=D / 2, bbox_c=[0.0, 0.0, H / 2])
    elif su == "cone":
        D1, D2, H = {"tiny": (4, 2, 3), "normal": (50, 20, 50), "huge": (2000, 800, 2000),
                     "extreme ratio": (6, 1, 1000)}[sc]
        D1, D2, H = f3(D1), f3(D2), f3(H)
        r1, r2 = D1 / 2, D2 / 2
        it.add("solid.cone", radius=r1, top_radius=r2, height=H, base=[0.0, 0.0, 0.0],
               operation="new", body_name=PART)
        fr.update(fz=H, loop=[[0.0, -r2, H]], ext0=r1, sides=[[0.0, -(r1 + r2) / 2, H / 2]],
                  rim=r2, bbox_c=[0.0, 0.0, H / 2])
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
        rf = math.sqrt(R * R - k * k)
        fr.update(fz=k, loop=[[0.0, -rf, k]], ext0=R, rim=rf, bbox_c=[0.0, 0.0, (k - R) / 2], sides=None)
    elif su == "torus":
        Dm, Dt = {"tiny": (3.6, 1.2), "normal": (60, 16), "huge": (3000, 800), "extreme ratio": (1000, 1)}[sc]
        Dm, Dt = f3(Dm), f3(Dt)
        Rm, r = Dm / 2, Dt / 2
        it.add("solid.torus", center=[0.0, 0.0, 0.0], major=Rm, minor=r, operation="new", body_name=PART)
        big = Rm + r + 1
        it.add("solid.box", length=2 * big, width=2 * big, height=r + 1, corner=[-big, -big, -(r + 1)],
               operation="cut", targets=[PART])
        # the flat face is at Z = 0 with the body above it, so features on it point down
        fr.update(fz=0.0, o=-1.0, cx=Rm, loop=[[0.0, -(Rm - r), 0.0], [0.0, -(Rm + r), 0.0]],
                  ext0=Rm + r, rim=Rm + r, bbox_c=[0.0, 0.0, r / 2], sides=None)
    return fr


def cyl(it, r, h, x, y, z_from, up, op, name=None, body_name=None):
    """A vertical cylinder of radius r and height h from z_from, going +Z if up else -Z."""
    p = {"radius": r, "height": h, "base": [x, y, z_from if up else z_from - h], "axis": "Z", "operation": op}
    if op != "new":
        p["targets"] = [PART]
    if name:
        p["name"] = name
    if body_name:
        p["body_name"] = body_name
    it.add("solid.cylinder", **p)


# ------------------------------------------------------------------ the steps
def prelude(it, to, su, sc, fr, e, g):
    """gen.prelude: the topology situation, at the gap g (a Decimal, mm; None without a contact)."""
    if to == "clean":
        return
    s, h = gen.sig(0.12 * e), gen.sig(0.12 * e)
    S, Hh = f3(s), f3(h)
    u0 = F(gen.ex(gen.inset(fr["hu"], e)))
    dd = f3(0.5 * min(fr["H"], e))
    cx, cy, fz, out = fr["cx"], fr["cy"], fr["fz"], fr["o"] > 0
    if to == "tangent edges":
        x = F(gen.fd(gen.D(fr["hu"]) - gen.D(s) / 2 - g))
        cyl(it, S / 2, Hh, cx + x, cy, fz, out, "join")
    elif to == "coplanar faces":
        z = fz if out else fz - Hh
        # the first pad centred u0 from C toward +X; the second beside it toward +Y, sharing a side
        it.add("solid.box", length=S, width=S, height=Hh, corner=[cx + u0 - S / 2, cy - S / 2, z],
               operation="join", targets=[PART])
        h2 = F(gen.fd(gen.D(h) + g))
        z2 = fz if out else fz - h2
        it.add("solid.box", length=S, width=S, height=h2, corner=[cx + u0 - S / 2, cy + S / 2, z2],
               operation="join", targets=[PART])
    elif to == "vertex on an edge":
        # an s x s pad turned 45 deg about its vertical axis, its +X corner (hu - g) from C toward +X:
        # built as its own body, turned about its centre, then joined
        xv = cx + F(gen.fd(gen.D(fr["hu"]) - g))
        xc = xv - S / math.sqrt(2)
        z = fz if out else fz - Hh
        it.add("solid.box", length=S, width=S, height=Hh, corner=[xc - S / 2, cy - S / 2, z],
               operation="new", body_name="Pad")
        it.add("solid.move", bodies=["Pad"], axis=[0.0, 0.0, 1.0], angle="45 deg", origin=[xc, cy, z])
        it.add("solid.combine", target=PART, tools=["Pad"], operation="join")
    elif to == "thin wall near tolerance":
        x = F(gen.fd(gen.D(fr["hu"]) - g - gen.D(s) / 2))
        cyl(it, S / 2, dd, cx + x, cy, fz, not out, "cut")
    elif to == "feature at a seam or pole":
        if fr["rim"] is None:
            raise Unsupported("seam notch on " + su)
        y = F(gen.fd(gen.D(s) / 2 + g))
        th = y / fr["rim"]  # the distance is measured along the circular edge from the seam (+X)
        cyl(it, S / 2, dd, fr["rim"] * math.cos(th), fr["rim"] * math.sin(th), fz, not out, "cut")
    elif to == "self-touching body":
        ap = F(gen.fd(gen.D(s) + g))
        for dy in (-ap / 2, ap / 2):
            cyl(it, S / 2, Hh, cx + u0, cy + dy, fz, out, "join")
    elif to == "multiple bodies":
        gap = f3(gen.sig(0.1 * e))
        cyl(it, S / 2, Hh, fr["ext0"] + gap + S / 2, 0.0, 0.0, True, "new", body_name="Extra")
        it.bodies.append("Extra")
    elif to == "edge chain around a corner":
        if su not in ("plane", "cylinder"):
            raise Unsupported("edge chain on " + su)
        z = fz - dd if out else fz
        it.add("solid.box", length=S, width=S, height=dd, corner=[-S / 2, fr["edge_py"] - S / 2, z],
               operation="cut", targets=[PART])


def hole_row(it, fr, x, d, dep, p):
    """A hole at (x, C.y - p), repeated 3 times along +Y at pitch p: holes at Y = -p, 0, +p."""
    name = it.name("Hole")
    cyl(it, d / 2, dep, x, fr["cy"] - p, fr["fz"], fr["o"] < 0, "cut", name=name)
    it.add("solid.pattern.rectangular", features=[name], dir1=[0.0, 1.0, 0.0], count1=3, spacing1=p)


def pattern_holes(it, fr, e):
    """gen.pattern_step."""
    d, p, dep = f3(gen.sig(0.1 * e)), f3(gen.sig(0.15 * e)), f3(gen.sig(0.4 * min(fr["H"], e)))
    hole_row(it, fr, fr["cx"] - F(gen.ex(gen.inset(fr["hu"], e))), d, dep, p)


def loop_intact(to):
    return to in ("clean", "coplanar faces", "self-touching body", "multiple bodies")


def fillet_loop(it, fr, r, to):
    if not loop_intact(to):
        raise Unsupported("fillet of a loop that the prelude splits")
    it.add("solid.fillet", edges=fr["loop"], radius=r)


def op_step(it, op, su, sc, fr, e, to):
    cx, cy, fz, out = fr["cx"], fr["cy"], fr["fz"], fr["o"] > 0
    d = f3(gen.sig(0.1 * e))
    dep = f3(gen.sig(0.4 * min(fr["H"], e)))
    r = f3(gen.sig(0.07 * e))
    w = f3(gen.sig(0.05 * e))
    c = f3(gen.sig(0.05 * e))
    p = f3(gen.sig(0.15 * e))
    if op == "extrude":
        cyl(it, d / 2, dep, cx, cy, fz, True, "join")  # the brief says "upward" for every face
    elif op == "cut":
        cyl(it, d / 2, dep, cx, cy, fz, not out, "cut")
    elif op == "shell":
        it.add("solid.shell", faces=[[cx, cy, fz]], thickness=w, body=PART)
    elif op == "fillet (constant)":
        fillet_loop(it, fr, r, to)
    elif op == "chamfer":
        if not loop_intact(to):
            raise Unsupported("chamfer of a loop that the prelude splits")
        it.add("solid.chamfer", edges=fr["loop"], distance=c, distance2=f3(2 * c))
    elif op == "draft":
        if not fr.get("sides"):
            raise Unsupported("draft on " + su)
        it.add("solid.draft", faces=fr["sides"], angle="3 deg", neutral="XY")
    elif op == "pattern (linear)":
        hole_row(it, fr, cx, d, dep, p)
    elif op == "pattern (circular)":
        if cx != 0.0 or cy != 0.0:
            raise Unsupported("circular pattern about an axis off the origin")
        name = it.name("Hole")
        cyl(it, d / 2, dep, cx + f3(1.5 * d), cy, fz, not out, "cut", name=name)
        it.add("solid.pattern.circular", features=[name], axis="Z", count=6, angle="360 deg")
    elif op == "mirror":
        name = it.name("Boss")
        z = fz if out else fz - dep
        it.add("solid.box", length=d, width=d, height=dep, corner=[cx - d / 2, cy + f3(1.5 * d) - d / 2, z],
               operation="join", targets=[PART], name=name)
        it.add("solid.mirror", features=[name], plane="XZ", combine=True)
    elif op == "boolean join":
        it.add("solid.sphere", center=[cx, cy, fz], radius=d, operation="join", targets=[PART])
    elif op == "boolean cut":
        it.add("solid.cylinder", radius=d, height=f3(8 * d), base=[cx, cy - f3(8 * d) / 2, fz - fr["o"] * d / 2],
               axis="Y", operation="cut", targets=[PART])
    elif op == "boolean intersect":
        if su == "torus":
            # gen.py: a sphere of 1.2 Wmin centred on the point C of the ring (#106)
            it.add("solid.sphere", center=[cx, cy, fz], radius=f3(1.2 * fr["Wmin"]) / 2,
                   operation="intersect", targets=[PART])
        else:
            rad = f3(1.2 * math.sqrt(fr["Wmin"] ** 2 + fr["H"] ** 2)) / 2
            it.add("solid.sphere", center=fr["bbox_c"], radius=rad, operation="intersect", targets=[PART])
    elif op == "thread":
        n, pi, ln = {"tiny": (1.6, 0.35, 1.6), "normal": (12, 1.75, 14), "huge": (100, 6, 120),
                     "extreme ratio": (1, 0.25, 0.5)}[sc]
        if not out:
            raise Unsupported("thread boss pointing down")
        cyl(it, n / 2, ln, cx, cy, fz, True, "join")
        it.add("solid.chamfer", edges=[[cx, cy - n / 2, fz + ln]], distance=pi)
        it.add("solid.thread", face=[cx, cy - n / 2, fz + ln / 2], designation="M%sx%s" % (gen.fmt(n), gen.fmt(pi)),
               modeled=True)
    elif op == "split":
        z = F(gen.fmt(gen.sig(fr["z0"] + 0.5 * fr["H"], 4)))
        it.add("solid.split_body", body=PART, plane={"origin": [0.0, 0.0, z], "normal": [0.0, 0.0, 1.0]})
        it.bodies.append(PART + " (1)")
    elif op == "offset face":
        it.add("solid.offset_face", faces=[[cx, cy, fz]], distance=dep)
    else:
        raise Unsupported(op + " (needs a sketch)" if op in (
            "revolve", "sweep", "loft", "fillet (variable)", "pattern (along a path)") else op)


def place(it, placement):
    """gen.placement_text: rotate 17 deg about X then 31 deg about Z, through the origin; then move
    10000 mm along +X."""
    rot = placement in ("rotated", "rotated + far")
    far = placement in ("far", "rotated + far")
    if rot:
        it.add("solid.move", bodies=list(it.bodies), axis=[1.0, 0.0, 0.0], angle="%d deg" % gen.ROT_X,
               origin=[0.0, 0.0, 0.0])
        it.add("solid.move", bodies=list(it.bodies), axis=[0.0, 0.0, 1.0], angle="%d deg" % gen.ROT_Z,
               origin=[0.0, 0.0, 0.0])
    if far:
        it.add("solid.move", bodies=list(it.bodies), translate=[float(gen.FAR_MM), 0.0, 0.0])


def script_for(item):
    op, su, to, sc, orr = item["operation"], item["surface"], item["topology"], item["scale"], item["order"]
    if op.startswith("sheet-metal"):
        raise Unsupported("sheet metal")
    g = gen.GAPS.get(item["gap"])
    it = Item()
    fr = body(it, su, sc)
    e = min(fr["Wmin"], 4 * fr["H"])
    # gen.brief_for's order: before-step, prelude, operation, after-step, placement
    if orr == "after fillet":
        fillet_loop(it, fr, f3(gen.sig(0.05 * e)), "clean")
    elif orr == "after shell":
        it.add("solid.shell", faces=[], thickness=f3(gen.sig(0.06 * e)), body=PART)
    elif orr == "after pattern":
        pattern_holes(it, fr, e)
    prelude(it, to, su, sc, fr, e, g)
    op_step(it, op, su, sc, fr, e, to)
    if orr == "before fillet":
        fillet_loop(it, fr, f3(gen.sig(0.05 * e)), to)
    elif orr == "before shell":
        it.add("solid.shell", faces=[], thickness=f3(gen.sig(0.06 * e)), body=PART)
    elif orr == "before pattern":
        pattern_holes(it, fr, e)
    place(it, item["placement"])
    return {"commands": it.cmds}


def items():
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "items.jsonl"), encoding="utf-8"):
        yield json.loads(line)


def write(out, chosen):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    n = ok = 0
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for item in chosen:
            n += 1
            try:
                rec = {"id": item["id"], "script": script_for(item), "unsupported": None, "expected": item["expected"]}
                ok += 1
            except Unsupported as why:
                rec = {"id": item["id"], "script": None, "unsupported": str(why), "expected": item["expected"]}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("wrote %d items to %s: %d with a script, %d without" % (n, out, ok, n - ok), file=sys.stderr)


# ------------------------------------------------------------------ check
TIMEOUT_S = 120


def first_error(text):
    for line in text.splitlines():
        if "error" in line.lower() or "panick" in line.lower():
            return line.strip()[:300]
    return text.strip().splitlines()[-1][:300] if text.strip() else ""


def measured(stdout):
    """The run's body count and total volume, from its JSON summary, and the README's `measured`."""
    try:
        data = json.loads(stdout[stdout.index("{"):])
    except ValueError:
        return None
    tot = data.get("total") or {}
    m = {"volume_mm3": tot.get("volume_mm3"), "bodies": data.get("body_count")}
    for k in ("faces", "edges", "vertices"):
        if isinstance(tot.get(k), int):
            m[k] = tot[k]
    return data.get("body_count"), tot.get("volume_mm3"), {k: v for k, v in m.items() if v is not None}


STEP_ERR = re.compile(r"^error: step \d+ \(([^)]+)\): (.*)$")


def signature(text):
    """The README's signature: the first error message as the CLI prints it, ' @ ', the failing command.
    A message that ends in a colon (an SMLib check) takes the next line, which names the failed test."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        m = STEP_ERR.match(line)
        if m:
            msg = m.group(2)
            if msg.endswith(":") and i + 1 < len(lines):
                msg += " " + lines[i + 1]
            return "%s @ %s" % (msg, m.group(1))
    return "%s @ solidcraft-cli run" % (first_error(text) or "no message")


def check_one(cli, tmp, r):
    """One item through the CLI, as one of the README's outcomes."""
    exp = r["expected"] or {}
    if not r["script"]:
        return {"id": r["id"], "outcome": "skipped", "detail": "no script: " + r["unsupported"]}
    path = os.path.join(tmp, r["id"] + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(r["script"], f)
    try:
        p = subprocess.run([cli, "run", path, "--param-check", "strict"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"id": r["id"], "outcome": "hang", "detail": "no result in %d s" % TIMEOUT_S,
                "signature": "no result in %d s @ the whole script" % TIMEOUT_S}
    text = p.stderr + p.stdout
    if p.returncode != 0:
        if "panick" in text.lower() or p.returncode not in (1, 2):
            return {"id": r["id"], "outcome": "crash", "detail": first_error(text) or "exit %d" % p.returncode,
                    "signature": signature(text)}
        if "param" in first_error(text).lower() and "check" in first_error(text).lower():
            return {"id": r["id"], "outcome": "skipped", "detail": "script refused by --param-check: " + first_error(text)}
        ok = exp.get("refusal_ok", False)
        return {"id": r["id"], "outcome": "correct-refusal" if ok else "wrong-refusal", "detail": first_error(text),
                "signature": signature(text)}
    got = measured(p.stdout)
    if not got:
        return {"id": r["id"], "outcome": "crash", "detail": "built, but printed no summary",
                "signature": "built, but printed no summary @ solidcraft-cli run"}
    bodies, vol, meas = got
    want = exp.get("volume_mm3")
    wrong = (exp.get("bodies") is not None and bodies != exp["bodies"]) or (
        want is not None and vol is not None and abs(vol - want) > 1e-6 * max(1.0, abs(want)) + 1e-4)
    detail = "bodies %s volume %r (expected %s, %s)" % (bodies, vol, exp.get("bodies"), want)
    if wrong:
        return {"id": r["id"], "outcome": "wrong-result", "detail": detail, "measured": meas,
                "signature": "%s bodies, volume %r where %s and %s are expected @ result" % (bodies, vol, exp.get("bodies"), want)}
    # without a closed-form volume only the body count is checked: say so
    return {"id": r["id"], "outcome": "correct", "detail": "" if want is not None else "body count only: " + detail,
            "measured": meas}


def cli_version(cli):
    try:
        return subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def check(cli, source, jobs=4, results=None, report="check.jsonl"):
    """Every item of `source` through the CLI, `jobs` at a time; the report ends sorted by item."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    recs = [json.loads(l) for l in open(source, encoding="utf-8")]
    counts, done = {}, []
    with open(report, "w", encoding="utf-8", newline="\n") as fh, tempfile.TemporaryDirectory() as tmp, \
            ThreadPoolExecutor(max_workers=jobs) as pool:
        for fut in as_completed([pool.submit(check_one, cli, tmp, r) for r in recs]):
            res = fut.result()
            done.append(res)
            counts[res["outcome"]] = counts.get(res["outcome"], 0) + 1
            fh.write(json.dumps(res) + "\n")
            fh.flush()
    done.sort(key=lambda x: x["id"])
    with open(report, "w", encoding="utf-8", newline="\n") as fh:
        for res in done:
            fh.write(json.dumps(res) + "\n")
    if results:
        version = cli_version(cli)
        os.makedirs(os.path.dirname(results) or ".", exist_ok=True)
        with open(results, "w", encoding="utf-8", newline="\n") as fh:
            for res in done:
                line = {"id": res["id"], "pipeline": "solidcraft", "outcome": res["outcome"],
                        "version": version, "build": version, "link": ""}
                for k in ("signature", "measured"):
                    if k in res:
                        line[k] = res[k]
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    print("checked %d items: %s" % (len(recs), counts), file=sys.stderr)


def main(argv):
    if argv and argv[0] == "--check":
        args = argv[1:]
        opts = {"--jobs": "4", "--results": None}
        for flag in list(opts):
            if flag in args:
                i = args.index(flag)
                opts[flag] = args[i + 1]
                del args[i:i + 2]
        check(args[0], args[1], int(opts["--jobs"]), opts["--results"])
    elif argv and argv[0] == "--batch":
        n = int(argv[1])
        lo, hi = 100 * (n - 1) + 1, 100 * n
        write("scripts/batch-%02d.jsonl" % n, [it for it in items() if lo <= int(it["id"][1:]) <= hi])
    else:
        write(argv[0] if argv else "scripts.jsonl", items())


if __name__ == "__main__":
    main(sys.argv[1:])
