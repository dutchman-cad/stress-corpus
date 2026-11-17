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
def spline_points(A, B):
    """gen.spline_text's 8 outline points, as the brief prints them."""
    pts = []
    for k in range(8):
        th = k * math.pi / 4
        f = 1 + 0.1 * math.sin(3 * th)
        pts.append([f3(0.5 * A * f * math.cos(th)), f3(0.5 * B * f * math.sin(th))])
    return pts


class PeriodicSpline:
    """The outline as SolidCraft builds it from sketch.spline.fit_point (periodic): a C2 cubic through the
    points, parameterised by chord length (its bounding box matches the kernel's to 1e-5 at every scale).
    Segment i runs from point i to point i + 1, so segment 0 leaves the +X point (the seam) toward +Y."""

    def __init__(self, pts):
        n = len(pts)
        self.p, self.h = pts, [math.dist(pts[(i + 1) % n], pts[i]) for i in range(n)]
        self.d = [self._slopes(k) for k in range(2)]

    def _slopes(self, k):
        n, p, h = len(self.p), self.p, self.h
        m = [[0.0] * n + [0.0] for _ in range(n)]
        for i in range(n):
            hp, hn = h[i - 1], h[i]
            m[i][(i - 1) % n] += hn
            m[i][i] += 2 * (hp + hn)
            m[i][(i + 1) % n] += hp
            m[i][n] = 3 * (hn * (p[i][k] - p[i - 1][k]) / hp + hp * (p[(i + 1) % n][k] - p[i][k]) / hn)
        for c in range(n):  # Gauss-Jordan; the matrix is diagonally dominant
            for r in range(n):
                if r != c:
                    f = m[r][c] / m[c][c]
                    m[r] = [x - f * y for x, y in zip(m[r], m[c])]
        return [m[i][n] / m[i][i] for i in range(n)]

    def at(self, i, t, deriv=False):
        n, h = len(self.p), self.h[i]
        if deriv:
            w = (6 * t * t - 6 * t, 3 * t * t - 4 * t + 1, -6 * t * t + 6 * t, 3 * t * t - 2 * t)
        else:
            w = (2 * t ** 3 - 3 * t * t + 1, t ** 3 - 2 * t * t + t, -2 * t ** 3 + 3 * t * t, t ** 3 - t * t)
        return [w[0] * self.p[i][k] + w[1] * h * self.d[k][i] + w[2] * self.p[(i + 1) % n][k]
                + w[3] * h * self.d[k][(i + 1) % n] for k in range(2)]

    def arc(self, i, t, steps=2000):
        """The length along segment i from its start to t (Simpson)."""
        if t <= 0:
            return 0.0
        dt = t / steps
        f = [math.hypot(*self.at(i, j * dt, True)) for j in range(steps + 1)]
        return dt / 3 * (f[0] + f[-1] + 4 * sum(f[1:-1:2]) + 2 * sum(f[2:-1:2]))

    def from_seam(self, y):
        """The point y along the curve from the +X point toward +Y, and the outward unit normal there."""
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if self.arc(0, mid) < y else (lo, mid)
        t = (lo + hi) / 2
        q, d = self.at(0, t), self.at(0, t, True)
        L = math.hypot(*d)
        return q, [d[1] / L, -d[0] / L]  # counter-clockwise outline: outward is the tangent turned clockwise

    def bbox_centre(self, steps=4000):
        xs, ys = [], []
        for i in range(len(self.p)):
            for j in range(steps + 1):
                x, y = self.at(i, j / steps)
                xs.append(x)
                ys.append(y)
        return [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]


def sheet_body(it, su, sc, b, fr):
    """gen.body(sheet=True): a flat sheet of thickness T on Z = 0, a rule with T, K 0.44 and bend radius
    T, and the base flange from the outline (an A x B rectangle, or the spline)."""
    A, B = {"tiny": (4, 3), "normal": (60, 40), "huge": (2400, 1600), "extreme ratio": (1000, 200)}[sc]
    A, B, T = f3(A), f3(B), f3(b["T"])
    it.add("sheet.manage_rules", name="Stock", thickness="%r mm" % T, k_factor=0.44, bend_radius="%r mm" % T,
           active=True)
    it.add("sketch.create", name="Base", plane={"origin": [0, 0, 0], "x_dir": [1, 0, 0], "y_dir": [0, 1, 0]})
    if su == "plane":
        it.add("sketch.rectangle.two_point", p0=[-A / 2, -B / 2], p1=[A / 2, B / 2])
        fr.update(loop=[[0.0, -B / 2, T], [A / 2, 0.0, T], [0.0, B / 2, T], [-A / 2, 0.0, T]], edge_py=B / 2,
                  sides=[[A / 2, 0.0, T / 2], [-A / 2, 0.0, T / 2], [0.0, B / 2, T / 2], [0.0, -B / 2, T / 2]])
    else:
        pts = spline_points(A, B)
        it.add("sketch.spline.fit_point", periodic=True, points=pts)
        fr.update(loop=[[0.0, pts[6][1], T]], edge_py=pts[2][1], sides=[[0.0, pts[6][1], T / 2]],
                  spline=PeriodicSpline(pts))
    it.add("sketch.finish")
    it.add("sheet.flange", sketch="Base", body_name=PART)
    fr.update(fz=T, ext0=A / 2, rim=None, bbox_c=[0.0, 0.0, T / 2] if su == "plane" else None,
              A=A, B=B, T=T, fh=f3(b["fh"]))
    return fr


def body(it, su, sc, sheet=False):
    """The base body, and its frame: the face's plane z and outward direction o (+1 up, -1 down),
    the face centre C, hu, H, the face's outer loop picks (off every seam and feature)."""
    b = gen.body(su, sc, sheet, None)
    fr = {"o": 1.0, "cx": 0.0, "cy": 0.0, "hu": b["hu"], "H": b["H"], "Wmin": b["Wmin"], "z0": b["z0"]}
    if sheet:
        return sheet_body(it, su, sc, b, fr)
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
    elif su == "spline":
        # gen.spline_text: a periodic cubic spline through 8 points, extruded H from Z = 0
        A, B, H = {"tiny": (4, 3, 2), "normal": (60, 40, 25), "huge": (2400, 1600, 1000),
                   "extreme ratio": (1000, 200, 1)}[sc]
        H = f3(H)
        pts = []
        for k in range(8):
            th = k * math.pi / 4
            f = 1 + 0.1 * math.sin(3 * th)
            pts.append([f3(0.5 * A * f * math.cos(th)), f3(0.5 * B * f * math.sin(th))])
        it.add("sketch.create", name="Outline", plane={"origin": [0, 0, 0], "x_dir": [1, 0, 0], "y_dir": [0, 1, 0]})
        it.add("sketch.spline.fit_point", periodic=True, points=pts)
        it.add("sketch.finish")
        it.add("solid.extrude", sketch="Outline", distance=H, operation="new", body_name=PART)
        # the -Y outline point (270 deg) and the +X point (0 deg) are on the curve; the bounding box
        # centre is not closed form, so a sphere at it is not scripted
        py = pts[6][1]
        # the +Y outline point (90 deg) is on the curve too: the edge chain's notch is centred there
        sp = PeriodicSpline(pts)
        fr.update(fz=H, loop=[[0.0, py, H]], ext0=pts[0][0], sides=[[0.0, py, H / 2]],
                  edge_py=pts[2][1], rim=None, spline=sp, bbox_c=sp.bbox_centre() + [H / 2])
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
        # the boss overhangs (g < 0): the loop runs round its base inside the face
        split_loop(fr, [[cx + x - S / 2, cy, fz]] if g < 0 else [], plus_x=True)
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
        if g < 0:
            # the corner pokes |g| beyond the edge: the loop's two pad edges are |g| sqrt 2 long, under the
            # tolerance, so a point cannot pick them apart from the outer edge
            fr["loop_unscripted"] = "fillet of a loop with sub-tolerance pad edges"
        split_loop(fr, [], plus_x=True)
    elif to == "thin wall near tolerance":
        x = F(gen.fd(gen.D(fr["hu"]) - g - gen.D(s) / 2))
        cyl(it, S / 2, dd, cx + x, cy, fz, not out, "cut")
        # the hole breaks out (g < 0): the loop runs round its inner side
        split_loop(fr, [[cx + x - S / 2, cy, fz]] if g < 0 else [], plus_x=True)
    elif to == "feature at a seam or pole":
        y = F(gen.fd(gen.D(s) / 2 + g))
        if fr.get("spline"):
            # centred on the curve y along it from the seam (the +X point); the loop runs round its inner side
            q, nrm = fr["spline"].from_seam(y)
            cyl(it, S / 2, dd, q[0], q[1], fz, not out, "cut")
            split_loop(fr, [[q[0] - nrm[0] * S / 2, q[1] - nrm[1] * S / 2, fz]], plus_x=False)
            return
        if fr["rim"] is None:
            raise Unsupported("seam notch on " + su)
        th = y / fr["rim"]  # the distance is measured along the circular edge from the seam (+X)
        cyl(it, S / 2, dd, fr["rim"] * math.cos(th), fr["rim"] * math.sin(th), fz, not out, "cut")
        # the notch is centred on the edge: the loop runs round its inner side
        ri = fr["rim"] - S / 2
        split_loop(fr, [[ri * math.cos(th), ri * math.sin(th), fz]], plus_x=False)
    elif to == "self-touching body":
        ap = F(gen.fd(gen.D(s) + g))
        for dy in (-ap / 2, ap / 2):
            cyl(it, S / 2, Hh, cx + u0, cy + dy, fz, out, "join")
    elif to == "multiple bodies":
        gap = f3(gen.sig(0.1 * e))
        cyl(it, S / 2, Hh, fr["ext0"] + gap + S / 2, 0.0, 0.0, True, "new", body_name="Extra")
        it.bodies.append("Extra")
    elif to == "edge chain around a corner":
        if su not in ("plane", "cylinder", "spline"):
            raise Unsupported("edge chain on " + su)
        z = fz - dd if out else fz
        it.add("solid.box", length=S, width=S, height=dd, corner=[-S / 2, fr["edge_py"] - S / 2, z],
               operation="cut", targets=[PART])
        # the loop runs round the notch's three edges in the face; the +Y edge is cut in two
        yb = fr["edge_py"] - S / 2
        # where the notch's sides meet the outer edge: the block's straight edge, the cylinder's circle;
        # on the spline, the curve stays within a hair of the +Y point over the notch's width
        ye = math.sqrt(fr["edge_py"] ** 2 - S * S / 4) if su == "cylinder" else fr["edge_py"]
        picks = [[0.0, yb, fz], [-S / 2, (yb + ye) / 2, fz], [S / 2, (yb + ye) / 2, fz]]
        if su == "plane":
            fr["loop"] = [q for q in fr["loop"] if not (q[0] == 0.0 and q[1] > 0)] + [
                [-fr["ext0"] / 2, fr["edge_py"], fz], [fr["ext0"] / 2, fr["edge_py"], fz]]
        fr["loop"] = fr["loop"] + picks


def hole_row(it, fr, x, d, dep, p):
    """A hole at (x, C.y - p), repeated 3 times along +Y at pitch p: holes at Y = -p, 0, +p."""
    name = it.name("Hole")
    cyl(it, d / 2, dep, x, fr["cy"] - p, fr["fz"], fr["o"] < 0, "cut", name=name)
    it.add("solid.pattern.rectangular", features=[name], dir1=[0.0, 1.0, 0.0], count1=3, spacing1=p)


def pattern_holes(it, fr, e):
    """gen.pattern_step."""
    d, p, dep = f3(gen.sig(0.1 * e)), f3(gen.sig(0.15 * e)), f3(gen.sig(0.4 * min(fr["H"], e)))
    hole_row(it, fr, fr["cx"] - F(gen.ex(gen.inset(fr["hu"], e))), d, dep, p)


def split_loop(fr, extra, plus_x):
    """The prelude cut into the face's outer loop: pick its new edges too. On a block, a feature at the +X
    edge's midpoint sits on (or within a gap of) the +X edge's own pick, so pick that edge a quarter of
    the way along instead, and both quarters when the feature cuts it in two."""
    loop = fr["loop"]
    if plus_x and fr["rim"] is None and fr.get("edge_py") is not None:  # the block
        q = [p for p in loop if p[0] > 0 and p[1] == 0.0]
        if q:
            b4 = fr["edge_py"] / 2
            loop = [p for p in loop if p not in q] + [[q[0][0], -b4, q[0][2]]] + (
                [[q[0][0], b4, q[0][2]]] if extra else [])
    fr["loop"] = loop + extra


def loop_picks(fr):
    if fr.get("loop_unscripted"):
        raise Unsupported(fr["loop_unscripted"])
    return fr["loop"]


def fillet_loop(it, fr, r, to):
    it.add("solid.fillet", edges=loop_picks(fr), radius=r)


def op_step(it, op, su, sc, fr, e, to):
    cx, cy, fz, out = fr["cx"], fr["cy"], fr["fz"], fr["o"] > 0
    d = f3(gen.sig(0.1 * e))
    dep = f3(gen.sig(0.4 * min(fr["H"], e)))
    r = f3(gen.sig(0.07 * e))
    w = f3(gen.sig(0.05 * e))
    c = f3(gen.sig(0.05 * e))
    p = f3(gen.sig(0.15 * e))
    def sketch(o, x, y):
        name = it.name("Sketch")
        it.add("sketch.create", name=name, plane={"origin": o, "x_dir": x, "y_dir": y})
        return name

    if op == "revolve":
        # a g x g square in the vertical plane through C, its centre 1.5 d from the vertical axis through
        # C, against the face on the material's side, turned 360 deg about that axis: a ring groove
        g, R = f3(gen.sig(0.04 * e)), f3(1.5 * d)
        sq = sketch([cx, cy, fz], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
        it.add("sketch.rectangle.two_point", p0=[R - g / 2, -g if out else 0.0], p1=[R + g / 2, 0.0 if out else g])
        it.add("sketch.finish")
        it.add("solid.revolve", sketch=sq, axis={"origin": [cx, cy, fz], "dir": [0.0, 0.0, 1.0]}, angle="360 deg",
               operation="cut", targets=[PART])
        return
    if op == "sweep":
        # a circle of d at C, along a 90 deg arc of radius 3 d leaving C straight up (+Z) and ending toward +Y
        prof = sketch([cx, cy, fz], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
        it.add("sketch.circle.center", center=[0.0, 0.0], radius=d / 2)
        it.add("sketch.finish")
        path = sketch([cx, cy, fz], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])
        R = f3(3 * d)
        # sketch arcs run counter-clockwise: up from C, the arc is drawn from its top end; on the torus's
        # flat face it leaves C going down, away from the body (#117)
        if out:
            it.add("sketch.arc.center_point", center=[R, 0.0], start=[R, R], end=[0.0, 0.0], id="arc")
        else:
            it.add("sketch.arc.center_point", center=[R, 0.0], start=[0.0, 0.0], end=[R, -R], id="arc")
        it.add("sketch.finish")
        it.add("solid.sweep", sketch=prof, path_sketch=path, path=["arc"], orientation="perpendicular",
               operation="join", targets=[PART])
        return
    if op == "loft":
        # a d x d square at C to a circle of d on the plane dep above it (+Z)
        s0 = sketch([cx, cy, fz], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
        it.add("sketch.rectangle.two_point", p0=[-d / 2, -d / 2], p1=[d / 2, d / 2])
        it.add("sketch.finish")
        s1 = sketch([cx, cy, fz + fr["o"] * dep], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])  # below, on the torus (#117)
        it.add("sketch.circle.center", center=[0.0, 0.0], radius=d / 2)
        it.add("sketch.finish")
        it.add("solid.loft", sections=[{"sketch": s0}, {"sketch": s1}], operation="join", targets=[PART])
        return
    if op == "extrude":
        cyl(it, d / 2, dep, cx, cy, fz, out, "join")  # away from the body: down on the torus's flat face (#117)
    elif op == "cut":
        cyl(it, d / 2, dep, cx, cy, fz, not out, "cut")
    elif op == "shell":
        it.add("solid.shell", faces=[[cx, cy, fz]], thickness=w, body=PART)
    elif op == "fillet (constant)":
        fillet_loop(it, fr, r, to)
    elif op == "fillet (variable)":
        # r at the loop's +X point, rising linearly to 2 r at the opposite point (halfway round), and back
        if su == "torus":
            # each of the two loops from its own +X point (the ring's inner and outer circle)
            if len(loop_picks(fr)) != 2:
                raise Unsupported("variable fillet of a torus loop that the prelude splits")
            for q, xs in zip(loop_picks(fr), (cx - fr["hu"], cx + fr["hu"])):
                it.add("solid.fillet", edges=[q], radius=r, type="variable", radius2=r,
                       start=[xs, 0.0, fz], mid=[[0.5, f3(2 * r)]])
        else:
            it.add("solid.fillet", edges=loop_picks(fr), radius=r, type="variable", radius2=r,
                   start=[cx + fr["hu"], cy, fz], mid=[[0.5, f3(2 * r)]])
    elif op == "chamfer":
        # c down the side, 2 c across the face. SolidCraft's distance runs along the first face, the one
        # facing up most: the face itself, except on the torus, whose flat face faces down
        across, side = f3(2 * c), c
        it.add("solid.chamfer", edges=loop_picks(fr), distance=across if out else side,
               distance2=side if out else across)
    elif op == "draft":
        if not fr.get("sides"):
            raise Unsupported("draft on " + su)
        it.add("solid.draft", faces=fr["sides"], angle="3 deg", neutral="XY")
    elif op == "pattern (linear)":
        hole_row(it, fr, cx, d, dep, p)
    elif op == "pattern (along a path)":
        # a hole at C, 4 in all (gen.py's "repeat it N times": N holes, as its circular pattern's volume
        # counts them), p apart along an arc of radius 4 p in the face that leaves C toward +X and curves
        # toward +Y; solid.pattern.path's distance spans the first hole to the last
        name = it.name("Hole")
        cyl(it, d / 2, dep, cx, cy, fz, not out, "cut", name=name)
        path = sketch([cx, cy, fz], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
        R = f3(4 * p)
        it.add("sketch.arc.center_point", center=[0.0, R], start=[0.0, 0.0], end=[R, R], id="arc")
        it.add("sketch.finish")
        it.add("solid.pattern.path", features=[name], path_sketch=path, path=["arc"], count=4, distance=f3(3 * p))
    elif op == "pattern (circular)":
        name = it.name("Hole")
        cyl(it, d / 2, dep, cx + f3(1.5 * d), cy, fz, not out, "cut", name=name)
        # about the vertical axis through C (off the origin on the torus's flat face)
        axis = "Z" if (cx == 0.0 and cy == 0.0) else {"origin": [cx, cy, fz], "dir": [0.0, 0.0, 1.0]}
        it.add("solid.pattern.circular", features=[name], axis=axis, count=6, angle="360 deg")
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
            if fr["bbox_c"] is None:
                raise Unsupported("sphere at the bounding-box centre of a spline body")
            # #117: the sphere reaches the features, which sit up to hu from the axis on the top face
            reach = 1.1 * math.sqrt(fr["hu"] ** 2 + (fr["H"] / 2.0) ** 2)
            rad = f3(max(1.2 * math.sqrt(fr["Wmin"] ** 2 + fr["H"] ** 2), 2 * reach)) / 2
            it.add("solid.sphere", center=fr["bbox_c"], radius=rad, operation="intersect", targets=[PART])
            if any(math.dist(q, fr["bbox_c"]) > rad for q in fr["loop"]):
                # the sphere trims the face's outer loop away where a pick was
                fr["loop_unscripted"] = "fillet of a loop that the intersecting sphere trims"
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
    elif op == "sheet-metal flange":
        # fh tall, 90 deg up, along the +X edge (picked a quarter along: a feature may sit at its middle)
        it.add("sheet.flange", type="edge", edges=[[fr["A"] / 2, -fr["B"] / 4, fr["T"]]], height="%r mm" % fr["fh"],
               angle="90 deg")
        fr["loop_unscripted"] = "blend of a loop after the sheet is bent"
    elif op == "sheet-metal bend":
        # fold the part beyond X = x up 90 deg about the line X = x, inside radius T (the rule's K 0.44)
        x = f3(gen.sig(0.2 * fr["A"]))
        it.add("sheet.fold", points=[[x, -fr["B"], fr["T"]], [x, fr["B"], fr["T"]]], angle="90 deg",
               radius="%r mm" % fr["T"])
        fr["loop_unscripted"] = "blend of a loop after the sheet is bent"
    elif op == "offset face":
        if to in gen.PADS_ON_FACE:
            dep = f3(gen.sig(0.5 * gen.sig(0.12 * e)))  # half the pad height: the pads stay proud (#117)
        it.add("solid.offset_face", faces=[[cx, cy, fz]], distance=dep)
        # the face moved out along its normal: so did its loop
        fr["loop"] = [[q[0], q[1], q[2] + fr["o"] * dep] for q in fr["loop"]]
        fr["fz"] = fz + fr["o"] * dep
    else:
        raise Unsupported(op)


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
    sheet = op.startswith("sheet-metal")
    if sheet and op == "sheet-metal flange" and su != "plane":
        raise Unsupported("flange along part of a spline edge")
    g = gen.GAPS.get(item["gap"])
    it = Item()
    fr = body(it, su, sc, sheet)
    e = min(fr["Wmin"], 4 * fr["H"])
    # gen.brief_for's order: before-step, prelude, operation, after-step, placement
    if orr == "after fillet":
        fillet_loop(it, fr, f3(gen.fillet_r(op, "first", e)), "clean")
    elif orr == "after shell":
        it.add("solid.shell", faces=[], thickness=f3(gen.sig(0.06 * e)), body=PART)
    elif orr == "after pattern":
        pattern_holes(it, fr, e)
    prelude(it, to, su, sc, fr, e, g)
    op_step(it, op, su, sc, fr, e, to)
    if orr == "before fillet":
        fillet_loop(it, fr, f3(gen.fillet_r(op, "last", e)), to)
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


def hang_step(cli, tmp, r):
    """The command a hung script was running when its time ran out. The CLI prints nothing until it
    finishes, so bisect on prefixes of the script: a prefix that hangs makes every longer one hang, so
    the shortest prefix that hangs ends in that command (about log2(commands) runs)."""
    cmds = r["script"].get("commands") or []
    path = os.path.join(tmp, r["id"] + ".prefix.json")
    done, hung = 0, len(cmds)  # the empty prefix finishes; the whole script hung
    while hung - done > 1:
        mid = (done + hung) // 2
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(r["script"], commands=cmds[:mid]), f)
        try:
            subprocess.run([cli, "run", path, "--param-check", "strict"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
            done = mid
        except subprocess.TimeoutExpired:
            hung = mid
    return cmds[hung - 1].get("command") if cmds else None


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
        step = hang_step(cli, tmp, r) or "the whole script"
        return {"id": r["id"], "outcome": "hang", "detail": "no result in %d s, running %s" % (TIMEOUT_S, step),
                "signature": "no result within %d s @ %s" % (TIMEOUT_S, step)}
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
