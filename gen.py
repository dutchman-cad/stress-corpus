#!/usr/bin/env python3
"""Deterministic generator for the stress corpus.

Items come from kernel axes, not object categories:

  operation x surface x topology x gap x scale x order x placement

Coverage, in this order (lowest IDs first):
  1. a seeded three-wise covering array over operation x topology x order
     (every feasible combination of those three appears in some item);
  2. a seeded all-pairs pass over all seven axes (every feasible pair of
     values from two different axes appears in some item);
  3. seeded random extra combinations up to COUNT.

Each item also carries an "expected" object computed from the same numbers
that appear in the brief: bodies, refusal_ok, and volume_mm3 only where it is
closed form for that exact combination (it is left out otherwise).

Writes items.jsonl: one JSON object per line.

No network, no AI. Same seed, same file, byte for byte:  python3 gen.py
"""
import hashlib
import itertools
import json
import math
import random
import sys
from decimal import Decimal

SEED = 20261116
COUNT = 10000

TOL = Decimal("1e-5")   # the kernel tolerance, absolute, in mm

# Contact-type topology values: the ones where "how far from touching" is a question.
CONTACT = [
    "tangent edges", "coplanar faces", "vertex on an edge",
    "thin wall near tolerance", "feature at a seam or pole", "self-touching body",
]
# Absolute gaps in mm. Positive = clear of the contact, negative = overlapping it.
GAPS = {
    "exact": Decimal("0"), "+1e-7": Decimal("1e-7"), "+1e-6": Decimal("1e-6"),
    "+tol": Decimal("1e-5"), "+2\u00b7tol": Decimal("2e-5"), "+1e-4": Decimal("1e-4"),
    "-1e-6": Decimal("-1e-6"), "-tol": Decimal("-1e-5"),
}
PLACEMENTS = ["origin", "rotated", "far", "rotated + far"]
# Placement is applied to the whole finished item, about / from the origin.
ROT_X, ROT_Z, FAR_MM = 17, 31, 10000
# Weights for the random fill only (the covering sets use every value). A contact
# topology is as likely as any other topology, whatever its number of gap values.
PLACEMENT_WEIGHT = {"origin": 6, "rotated": 1.5, "far": 1.5, "rotated + far": 1}

AXES = {
    "operation": [
        "extrude", "cut", "revolve", "sweep", "loft", "shell",
        "fillet (constant)", "fillet (variable)", "chamfer", "draft",
        "pattern (linear)", "pattern (circular)", "pattern (along a path)",
        "mirror", "boolean join", "boolean cut", "boolean intersect",
        "thread", "sheet-metal flange", "sheet-metal bend", "split",
        "offset face",
    ],
    "surface": ["plane", "cylinder", "cone", "sphere", "torus", "spline"],
    "topology": [
        "clean", "tangent edges", "coplanar faces", "vertex on an edge",
        "thin wall near tolerance", "feature at a seam or pole",
        "self-touching body", "multiple bodies", "edge chain around a corner",
    ],
    "gap": list(GAPS) + ["none"],
    "scale": ["tiny", "normal", "huge", "extreme ratio"],
    "order": [
        "none", "before fillet", "after fillet", "before shell",
        "after shell", "before pattern", "after pattern",
    ],
    "placement": PLACEMENTS,
}
KEYS = list(AXES)
IOP, ISU, ITO, IGAP, ISC, IOR, IPL = range(7)


def feasible(row):
    op, su, to, gp, sc, orr, pl = row
    if (to in CONTACT) != (gp != "none"):
        return False
    if op.startswith("sheet-metal"):
        if su not in ("plane", "spline") or orr in ("before shell", "after shell"):
            return False
        if to == "thin wall near tolerance":
            return False
    if op == "thread" and su not in ("plane", "cylinder", "cone"):
        return False
    if op == "draft" and su in ("sphere", "torus"):
        return False
    if to == "feature at a seam or pole" and su == "plane":
        return False
    if to == "edge chain around a corner" and su not in ("plane", "cylinder", "spline"):
        return False
    if op == "shell" and orr in ("before shell", "after shell"):
        return False
    if op.startswith("fillet") and orr in ("before fillet", "after fillet"):
        return False
    if op.startswith("pattern") and orr in ("before pattern", "after pattern"):
        return False
    return True


# ------------------------------------------------------------------ numbers
def sig(x, n=2):
    if x == 0:
        return 0.0
    return round(x, -int(math.floor(math.log10(abs(x)))) + (n - 1))


def fmt(x):
    x = sig(x, 3) if abs(x) < 1e6 else x
    if x == int(x) and abs(x) < 1e9:
        return str(int(x))
    s = ("%.6f" % x).rstrip("0").rstrip(".")
    return s


# ------------------------------------------------------------------ bodies
TUBE_T = {"tiny": 0.2, "normal": 1.5, "huge": 6, "extreme ratio": 1}


def body(surface, scale, sheet, rng):
    """Return dict: text, F (face name), C (centre phrase), hu, Wmin, H, loop."""
    d = {"F": "top face", "loop": None, "C": None, "z0": 0.0}
    if sheet:
        A, B = {"tiny": (4, 3), "normal": (60, 40), "huge": (2400, 1600),
                "extreme ratio": (1000, 200)}[scale]
        T = TUBE_T[scale]
        if surface == "plane":
            d["text"] = ("a flat %s × %s mm sheet of thickness %s mm, centred on the origin in X and Y, "
                         "with its bottom face on Z = 0 (it fills Z = 0 to %s mm)") % (fmt(A), fmt(B), fmt(T), fmt(T))
            d["Wmin"], d["hu"] = min(A, B), A / 2.0
        else:
            d["text"] = ("a flat sheet of thickness %s mm with a closed spline outline (see below), centred on the origin in X and Y, "
                         "with its bottom face on Z = 0 (it fills Z = 0 to %s mm)") % (fmt(T), fmt(T))
            d["Wmin"], d["hu"] = 0.8 * B, A / 2.0
            d["text"] += spline_text(A, B)
        d["H"] = T
        d["fh"] = sig(0.15 * d["Wmin"])
        d["A"] = A
        d["T"] = T
    elif surface == "plane":
        A, B, H = {"tiny": (4, 3, 2), "normal": (60, 40, 25), "huge": (2400, 1600, 1000),
                   "extreme ratio": (1000, 200, 1)}[scale]
        d["text"] = "a %s × %s × %s mm block, centred on the origin and resting on Z = 0" % (fmt(A), fmt(B), fmt(H))
        d.update(Wmin=min(A, B), hu=A / 2.0, H=H)
    elif surface == "cylinder":
        D, H = {"tiny": (3, 4), "normal": (40, 60), "huge": (1200, 2400),
                "extreme ratio": (1, 1000)}[scale]
        d["text"] = "a Ø%s × %s mm cylinder with a vertical axis through the origin, resting on Z = 0" % (fmt(D), fmt(H))
        d.update(Wmin=D, hu=D / 2.0, H=H)
    elif surface == "cone":
        D1, D2, H = {"tiny": (4, 2, 3), "normal": (50, 20, 50), "huge": (2000, 800, 2000),
                     "extreme ratio": (6, 1, 1000)}[scale]
        d["text"] = "a cone frustum with a vertical axis through the origin, Ø%s mm at the bottom (Z = 0), Ø%s mm at the top, %s mm tall" % (fmt(D1), fmt(D2), fmt(H))
        d.update(Wmin=D2, hu=D2 / 2.0, H=H)
    elif surface == "sphere":
        D = {"tiny": 4, "normal": 50, "huge": 2400, "extreme ratio": 1000}[scale]
        R = D / 2.0
        if scale == "extreme ratio":
            fr = 0.5
            k = math.sqrt(R * R - fr * fr)
            flat = 1.0
            gap = R - k
            cut = ("the plane Z = %s mm, which is %s mm below the pole and leaves a flat of Ø%s mm" % (
                ("%.7f" % k).rstrip("0"), ("%.5f" % gap).rstrip("0"), fmt(flat)))
        else:
            k = 0.6 * R
            flat = 2 * math.sqrt(R * R - k * k)
            cut = "the plane Z = %s mm, leaving a flat of Ø%s mm" % (fmt(k), fmt(flat))
        d["text"] = "a sphere of Ø%s mm centred on the origin, with the top removed by %s; the body is the part below that plane" % (fmt(D), cut)
        d.update(Wmin=flat, hu=flat / 2.0, H=R + k, F="flat cap face", z0=-R)
    elif surface == "torus":
        Dm, Dt = {"tiny": (3.6, 1.2), "normal": (60, 16), "huge": (3000, 800),
                  "extreme ratio": (1000, 1)}[scale]
        d["text"] = ("the upper half (Z ≥ 0) of a torus about the Z axis: centre-circle Ø%s mm, tube Ø%s mm, "
                     "cut by the plane Z = 0, so the body has a flat annular face at Z = 0") % (fmt(Dm), fmt(Dt))
        d.update(Wmin=Dt, hu=Dt / 2.0, H=Dt / 2.0, F="flat cut face",
                 C="the point on the centre circle of the flat cut face at +X (X = %s mm, Y = 0)" % fmt(Dm / 2.0),
                 loop="both edge loops (inner and outer circle) of the flat cut face")
    else:  # spline
        A, B, H = {"tiny": (4, 3, 2), "normal": (60, 40, 25), "huge": (2400, 1600, 1000),
                   "extreme ratio": (1000, 200, 1)}[scale]
        d["text"] = "a %s mm tall extrusion, from Z = 0, of a closed spline outline (see below)%s" % (fmt(H), spline_text(A, B))
        d.update(Wmin=0.8 * B, hu=A / 2.0, H=H)
    if d["C"] is None:
        d["C"] = "the centre of the %s" % d["F"]
    if d["loop"] is None:
        d["loop"] = "the outer edge loop of the %s" % d["F"]
    return d


def spline_text(A, B):
    pts = []
    for k in range(8):
        th = k * math.pi / 4
        f = 1 + 0.1 * math.sin(3 * th)
        pts.append("(%s, %s)" % (fmt(0.5 * A * f * math.cos(th)), fmt(0.5 * B * f * math.sin(th))))
    return " [a closed periodic cubic spline through the points (X, Y) mm: %s]" % ", ".join(pts)


# ------------------------------------------------------------------ steps
def ex(x):
    """Exact number (no significant-figure rounding), for tangent/edge positions."""
    x = round(x, 6)
    return str(int(x)) if x == int(x) else ("%.6f" % x).rstrip("0").rstrip(".")


def inset(hu, e):
    """Offset along +X, from the local origin, for a small feature that must not touch the edge."""
    return hu - 0.1 * e if hu <= 1.2 * e else 0.6 * hu


def D(x):
    """Exact decimal for a float that was built from short decimal numbers."""
    return Decimal(repr(round(float(x), 9)))


def fd(x):
    """Format a Decimal in plain notation (no exponent), exact."""
    t = format(x.normalize(), "f")
    return "0" if t in ("-0", "") else t


def prelude(topo, b, e, scale, g):
    """Setup step that creates the topology situation. Sizes from e.

    g is the gap in mm (Decimal, absolute): positive = clear of the contact,
    negative = overlapping it. It is None for topologies without a contact."""
    F, hu, C = b["F"], b["hu"], b["C"]
    s = sig(0.12 * e)
    h = sig(0.12 * e)
    u0 = ex(inset(hu, e))
    dd = fmt(0.5 * min(b["H"], e))
    if topo == "clean":
        return ""
    ag = fd(abs(g)) if g is not None else ""
    if topo == "tangent edges":
        x = fd(D(hu) - D(s) / 2 - g)
        if g == 0:
            tail = "so its side is exactly tangent to the face's outer edge."
        elif g > 0:
            tail = "so its side is %s mm inside the face's outer edge." % ag
        else:
            tail = "so its side overhangs the face's outer edge by %s mm." % ag
        return ("Add a \u00d8%s mm boss, %s mm tall, on the %s with its centre %s mm from %s toward +X, %s" % (
            fmt(s), fmt(h), F, x, C, tail))
    if topo == "coplanar faces":
        if g == 0:
            tail = "so the two pad tops are exactly coplanar and the two pads touch face to face."
            second = "a second identical pad"
        elif g > 0:
            second = "a second pad of the same footprint that is %s mm taller" % ag
            tail = "so its top sits %s mm above the first pad's top and the two pads touch face to face." % ag
        else:
            second = "a second pad of the same footprint that is %s mm shorter" % ag
            tail = "so its top sits %s mm below the first pad's top and the two pads touch face to face." % ag
        return ("Add a %s \u00d7 %s mm square pad, %s mm tall, on the %s with its centre %s mm from %s toward +X, "
                "and %s directly beside it toward +Y sharing one side face, %s" % (
                    fmt(s), fmt(s), fmt(h), F, u0, C, second, tail))
    if topo == "vertex on an edge":
        if g == 0:
            tail = "with its +X corner vertex exactly on the face's outer edge at the +X point."
        elif g > 0:
            tail = "with its +X corner vertex %s mm inside the face's outer edge at the +X point." % ag
        else:
            tail = "with its +X corner vertex %s mm beyond the face's outer edge at the +X point." % ag
        return ("Add a %s \u00d7 %s mm square pad, %s mm tall, on the %s, rotated 45\u00b0 about the vertical axis, %s" % (
            fmt(s), fmt(s), fmt(h), F, tail))
    if topo == "thin wall near tolerance":
        x = fd(D(hu) - g - D(s) / 2)
        if g == 0:
            tail = "so the hole's side is exactly tangent to the outer edge (no wall)."
        elif g > 0:
            tail = "so only a %s mm wall remains between the hole and the outer edge." % ag
        else:
            tail = "so the hole breaks out through the outer edge, %s mm beyond it." % ag
        return ("Drill a \u00d8%s mm blind hole, %s mm deep, in the %s with its centre %s mm from %s toward +X, %s" % (
            fmt(s), dd, F, x, C, tail))
    if topo == "feature at a seam or pole":
        seam = {"cylinder": "the cylinder's 0\u00b0 seam line (at +X)",
                "cone": "the cone's 0\u00b0 seam line (at +X)",
                "sphere": "the sphere's 0\u00b0 meridian seam (at +X)",
                "torus": "the torus's 0\u00b0 meridian seam (at +X)",
                "spline": "the point where the periodic spline closes (the +X point)"}[b["su"]]
        y = fd(D(s) / 2 + g)
        if g == 0:
            tail = "so its side lies exactly on the seam."
        elif g > 0:
            tail = "so its nearest side is %s mm clear of the seam." % ag
        else:
            tail = "so its nearest side overlaps the seam by %s mm." % ag
        return ("Cut a \u00d8%s mm notch, %s mm deep, centred on the outer edge of the %s, %s mm along that edge "
                "toward +Y from %s, %s" % (fmt(s), dd, F, y, seam, tail))
    if topo == "self-touching body":
        ap = fd(D(s) + g)
        if g == 0:
            tail = "so they touch along a single vertical line while both fuse to the face (the body touches itself there)."
        elif g > 0:
            tail = "so a %s mm gap separates them while both fuse to the face." % ag
        else:
            tail = "so they overlap by %s mm while both fuse to the face." % ag
        return ("Add two \u00d8%s mm cylinders, %s mm tall, standing on the %s with centres %s mm from %s toward +X "
                "and %s mm apart in Y, %s" % (fmt(s), fmt(h), F, u0, C, ap, tail))
    if topo == "multiple bodies":
        gap = sig(0.1 * e)
        return ("Add a separate \u00d8%s mm cylinder body, %s mm tall, standing on Z = 0 with its nearest side %s mm outside "
                "the body toward +X, and do not merge it." % (fmt(s), fmt(h), fmt(gap)))
    if topo == "edge chain around a corner":
        return ("Cut a %s \u00d7 %s mm square notch, %s mm deep, with its centre exactly on the outer edge of the %s at +Y "
                "(the notch corners turn the edge chain)." % (fmt(s), fmt(s), dd, F))
    raise ValueError(topo)


def pattern_step(b, e):
    F, C = b["F"], b["C"]
    d, p, dep = sig(0.1 * e), sig(0.15 * e), sig(0.4 * min(b["H"], e))
    u = ex(inset(b["hu"], e))
    return ("cut a \u00d8%s mm hole, %s mm deep, in the %s %s mm from %s toward -X, then repeat it "
            "in a linear pattern of 3 along Y at %s mm pitch, so the holes are at Y = -%s, 0 and +%s mm" % (
                fmt(d), fmt(dep), F, u, C, fmt(p), fmt(p), fmt(p)))


# Topologies whose prelude stands pads, bosses or cylinders on the top face.
PADS_ON_FACE = ("tangent edges", "coplanar faces", "vertex on an edge", "self-touching body")


def op_step(op, b, e, scale, to=None, g=None):
    F, C, loop = b["F"], b["C"], b["loop"]
    tor = b["su"] == "torus"   # its flat face faces -Z, the body is above it
    d = sig(0.1 * e)
    dep = sig(0.4 * min(b["H"], e))
    r = sig(0.07 * e)
    w = sig(0.05 * e)
    c = sig(0.05 * e)
    p = sig(0.15 * e)
    if op == "extrude":
        return "Extrude a Ø%s mm circle sketched at %s %s %s mm to add a boss." % (
            fmt(d), C, "downward, away from the body (toward -Z)," if tor else "upward", fmt(dep))
    if op == "cut":
        return "Cut a Ø%s mm hole at %s, %s mm deep." % (fmt(d), C, fmt(dep))
    if op == "revolve":
        g = sig(0.04 * e)
        return ("Sketch a %s × %s mm square whose centre is %s mm from the vertical axis through %s, and revolve it 360° "
                "about that axis to cut a ring groove into the %s." % (fmt(g), fmt(g), fmt(1.5 * d), C, F))
    if op == "sweep":
        return ("Sweep a Ø%s mm circle along a 90° arc of radius %s mm that leaves %s going straight %s and ends heading toward +Y, "
                "to add a bent tube." % (fmt(d), fmt(3 * d), C, "down, away from the body" if tor else "up"))
    if op == "loft":
        return ("Loft a neck %s mm tall from a %s × %s mm square sketched at %s to a Ø%s mm circle on a plane %s mm %s." % (
            fmt(dep), fmt(d), fmt(d), C, fmt(d), fmt(dep),
            "below it (toward -Z, away from the body)" if tor else "above it"))
    if op == "shell":
        return "Hollow the body to %s mm walls, removing the %s." % (fmt(w), F)
    if op == "fillet (constant)":
        return "Fillet %s with a constant %s mm radius." % (loop, fmt(r))
    if op == "fillet (variable)":
        return ("Fillet %s with a variable radius: %s mm at the +X point, rising linearly to %s mm at the opposite point." % (
            loop, fmt(r), fmt(2 * r)))
    if op == "chamfer":
        return "Chamfer %s with an unequal chamfer, %s mm down the side and %s mm across the face." % (loop, fmt(c), fmt(2 * c))
    if op == "draft":
        return "Draft all side faces by 3°, with the bottom face as the neutral plane, so the body narrows toward the %s." % F
    if op == "pattern (linear)":
        return "Cut a Ø%s mm hole at %s, %s mm deep, then repeat it in a linear pattern of 3 along Y at %s mm pitch, so the holes are at Y = -%s, 0 and +%s mm." % (
            fmt(d), C, fmt(dep), fmt(p), fmt(p), fmt(p))
    if op == "pattern (circular)":
        return ("Cut a Ø%s mm hole %s mm deep, centred %s mm from the vertical axis through %s, then repeat it 6 times in a circular pattern about that axis." % (
            fmt(d), fmt(dep), fmt(1.5 * d), C))
    if op == "pattern (along a path)":
        return ("Cut a Ø%s mm hole %s mm deep at %s, then repeat it 4 times, %s mm apart measured along a path, following an arc "
                "in the %s that curves toward +Y with a %s mm radius." % (fmt(d), fmt(dep), C, fmt(p), F, fmt(4 * p)))
    if op == "mirror":
        return ("Add a %s × %s mm square boss, %s mm tall, centred %s mm from %s toward +Y, then mirror it across the XZ plane through the face centre." % (
            fmt(d), fmt(d), fmt(dep), fmt(1.5 * d), C))
    if op == "boolean join":
        return "Make a separate Ø%s mm sphere centred on the %s at %s and join it to the body." % (fmt(2 * d), F, C)
    if op == "boolean cut":
        return "Make a separate Ø%s mm cylinder, %s mm long, lying along the Y axis with its axis %s mm %s at %s, and subtract it from the body." % (
            fmt(2 * d), fmt(8 * d), fmt(d / 2.0),
            "above the %s (inside the body)" % F if tor else "below the %s" % F, C)
    if op == "boolean intersect":
        if b["su"] == "torus":
            return ("Make a separate sphere of Ø%s mm centred on %s, and keep only the intersection of the body with it." % (
                fmt(1.2 * b["Wmin"]), C))
        # the sphere must reach the features, which sit up to hu from the axis and on the top face
        reach = 1.1 * math.sqrt(b["hu"] ** 2 + (b["H"] / 2.0) ** 2)
        dia = max(1.2 * math.sqrt(b["Wmin"] ** 2 + b["H"] ** 2), 2 * reach)
        return ("Make a separate sphere of Ø%s mm centred on the body's bounding-box centre, and keep only the intersection of the body with it." % (
            fmt(dia)))
    if op == "thread":
        n, pi, ln = {"tiny": (1.6, 0.35, 1.6), "normal": (12, 1.75, 14), "huge": (100, 6, 120),
                     "extreme ratio": (1, 0.25, 0.5)}[scale]
        return ("Add a Ø%s mm boss, %s mm tall, at %s and model an M%s × %s external thread over its full length, "
                "with a %s mm chamfer at the tip." % (fmt(n), fmt(ln), C, fmt(n), fmt(pi), fmt(pi)))
    if op == "sheet-metal flange":
        if b["su"] == "plane":
            where = "along the +X edge"
        else:
            where = "along the spline edge between the outline points at -45° and +45° (around +X)"
        return ("Bend a %s mm tall flange up 90° %s, with an inside bend radius of %s mm (the thickness) and a K-factor of 0.44." % (
            fmt(b["fh"]), where, fmt(b["T"])))
    if op == "sheet-metal bend":
        x = sig(0.2 * b["A"])
        return ("Fold the part of the sheet beyond X = %s mm up by 90° about the straight bend line X = %s mm (parallel to Y), "
                "inside radius %s mm, K-factor 0.44." % (fmt(x), fmt(x), fmt(b["T"])))
    if op == "split":
        z = sig(b["z0"] + 0.5 * b["H"], 4)
        return "Split the body with the plane Z = %s mm into two bodies and keep both." % fmt(z)
    if op == "offset face":
        if to in PADS_ON_FACE:
            dep = sig(0.5 * sig(0.12 * e))   # half the pad height: the pads stay proud of the moved face
        return "Offset the %s outward by %s mm (move the face along its normal, keeping the side faces connected)." % (F, fmt(dep))
    raise ValueError(op)


def fillet_r(op, which, e):
    """Radius of the loop fillet an order step adds. which is "first" (the fillet is
    done before the operation) or "last". A shell that removes the face leaves a rim
    one wall wide: a fillet after it is half the wall, one before it is twice the wall,
    so the inner radius never drops to zero."""
    if op == "shell":
        return sig(0.1 * e) if which == "first" else sig(0.025 * e)
    return sig(0.05 * e)


def hollow(b, e, op, to):
    t = "Hollow the %s to %s mm walls leaving a closed internal void (no face removed)." % (
        "upper body (the one above the split plane)" if op == "split" else
        "main body (not the separate cylinder)" if to == "multiple bodies" else "body", fmt(sig(0.06 * e)))
    if op == "split":
        t += " Leave the lower body as it is."
    return t


def order_before(order, b, e, op=None, to=None):
    if order == "after fillet":
        return "Fillet %s with %s mm." % (b["loop"], fmt(fillet_r(op, "first", e)))
    if order == "after shell":
        return hollow(b, e, None, to)
    if order == "after pattern":
        return cap(pattern_step(b, e)) + "."
    return ""


def order_after(order, b, e, op=None, to=None):
    up = " on the upper body (the one with the original top face)" if op == "split" else ""
    if order == "before fillet":
        return "Fillet %s%s with %s mm." % (b["loop"], up, fmt(fillet_r(op, "last", e)))
    if order == "before shell":
        return hollow(b, e, op, to)
    if order == "before pattern":
        return cap(pattern_step(b, e)) + (up or "") + "."
    return ""


def cap(t):
    return t[0].upper() + t[1:]


OBJECTS = {
    "plane": ["bracket", "spacer block", "shim", "label plate", "tile", "panel", "base plate", "bookend"],
    "cylinder": ["peg", "rod", "knob blank", "pin", "spacer", "handle", "bushing", "bollard"],
    "cone": ["funnel", "nozzle", "lamp shade", "traffic cone", "tip"],
    "sphere": ["ball", "bead", "dome cover", "finial", "ball joint housing"],
    "torus": ["ring", "O-ring seat", "hoop", "bangle", "donut"],
    "spline": ["guitar pick", "leaf", "paddle", "pebble", "key fob", "cam"],
}


# ------------------------------------------------------------------ placement
def rot_axes():
    a, c = math.radians(ROT_X), math.radians(ROT_Z)
    ca, sa, cc, sc_ = math.cos(a), math.sin(a), math.cos(c), math.sin(c)
    xi = (cc, sc_, 0.0)
    zi = (sc_ * sa, -cc * sa, ca)
    return xi, zi


def vec(v):
    return "(" + ", ".join(("%.6f" % x if abs(x) > 5e-7 else "0") for x in v) + ")"


def placement_text(pl):
    xi, zi = rot_axes()
    rot = ("rotate everything %d\u00b0 about X, then %d\u00b0 about Z, both through the origin (right-hand rule): "
           "the original X axis then points along %s and the original Z axis along %s" % (ROT_X, ROT_Z, vec(xi), vec(zi)))
    far = "the original origin lands at (%d, 0, 0)" % FAR_MM
    if pl == "origin":
        return ""
    if pl == "rotated":
        return "Finally, " + rot + "."
    if pl == "far":
        return "Finally, move everything %d mm along +X: %s." % (FAR_MM, far)
    return "Finally, " + rot + "; then move everything %d mm along +X: %s." % (FAR_MM, far)


# ------------------------------------------------------------------ expected values
def pf(x):
    """The number as printed in a brief."""
    return float(fmt(x))


def geom(surface, scale, sheet):
    """Body volume (closed form, or None) and the top-face footprint."""
    if sheet or surface == "spline":
        return {"V0": None, "shape": None}
    if surface == "plane":
        A, B, H = {"tiny": (4, 3, 2), "normal": (60, 40, 25), "huge": (2400, 1600, 1000),
                   "extreme ratio": (1000, 200, 1)}[scale]
        return {"V0": A * B * H, "shape": ("rect", A / 2.0, B / 2.0), "A": A, "B": B, "H": H}
    if surface == "cylinder":
        Dm, H = {"tiny": (3, 4), "normal": (40, 60), "huge": (1200, 2400),
                 "extreme ratio": (1, 1000)}[scale]
        return {"V0": math.pi * Dm * Dm / 4 * H, "shape": ("disc", Dm / 2.0), "R": Dm / 2.0, "H": H}
    if surface == "cone":
        D1, D2, H = {"tiny": (4, 2, 3), "normal": (50, 20, 50), "huge": (2000, 800, 2000),
                     "extreme ratio": (6, 1, 1000)}[scale]
        v = math.pi * H / 12.0 * (D1 * D1 + D1 * D2 + D2 * D2)
        return {"V0": v, "shape": ("disc", D2 / 2.0), "R1": D1 / 2.0, "R2": D2 / 2.0, "H": H}
    if surface == "sphere":
        Dm = {"tiny": 4, "normal": 50, "huge": 2400, "extreme ratio": 1000}[scale]
        R = Dm / 2.0
        if scale == "extreme ratio":
            k = math.sqrt(R * R - 0.25)
            hc = 0.25 / (R + k)
            flat = 1.0
        else:
            k = 0.6 * R
            hc = R - k
            flat = 2 * math.sqrt(R * R - k * k)
        v = 4.0 / 3.0 * math.pi * R ** 3 - math.pi * hc * hc * (3 * R - hc) / 3.0
        return {"V0": v, "shape": ("disc", flat / 2.0)}
    Dm, Dt = {"tiny": (3.6, 1.2), "normal": (60, 16), "huge": (3000, 800),
              "extreme ratio": (1000, 1)}[scale]
    return {"V0": math.pi ** 2 * (Dm / 2.0) * (Dt / 2.0) ** 2, "shape": ("disc", Dt / 2.0)}


def inside(shape, disc):
    x, y, r = disc
    if shape[0] == "rect":
        return abs(x) + r <= shape[1] and abs(y) + r <= shape[2]
    return math.hypot(x, y) + r <= shape[1]


def lens(r, dist):
    if dist >= 2 * r:
        return 0.0
    return 2 * r * r * math.acos(dist / (2 * r)) - dist / 2.0 * math.sqrt(4 * r * r - dist * dist)


def rim_fillet(R, r):
    """Volume removed by a constant fillet of radius r on a cylinder's top rim."""
    A = (1 - math.pi / 4) * r * r
    xbar = (r * r * r / 2 - math.pi * r * r / 4 * (r - 4 * r / (3 * math.pi))) / A
    return 2 * math.pi * (R - xbar) * A


def expected_for(row, g, b, e, geo, hit=False):
    """The expected object for an item. Volume only where closed form."""
    op, su, to, gp, sc, orr, pl = row
    bodies = 1 + (1 if to == "multiple bodies" else 0) + (1 if op == "split" else 0)
    if to == "thin wall near tolerance":
        refusal = g < TOL
    elif to in CONTACT:
        refusal = abs(g) < TOL
    else:
        refusal = False
    if hit or (op == "sheet-metal flange" and su == "spline"):
        refusal = True      # a partial flange on a curved edge, or a fillet that runs into a feature
    out = {}
    v = volume_for(row, g, b, e, geo)
    if v is not None:
        out["volume_mm3"] = float("%.9g" % v)
    out["bodies"] = bodies
    out["refusal_ok"] = bool(refusal)
    return out


def volume_for(row, g, b, e, geo):
    op, su, to, gp, sc, orr, pl = row
    V = geo["V0"]
    shape = geo["shape"]
    if V is None or op.startswith("sheet-metal"):
        return None
    gf = float(g) if g is not None else 0.0
    hu, H = b["hu"], b["H"]
    feats = []      # (discs, must_be_inside_face)
    rim = []        # radii of rim modifications
    s = pf(sig(0.12 * e))
    h = pf(sig(0.12 * e))
    dd = pf(0.5 * min(H, e))
    # ---- topology
    if to == "tangent edges":
        V += math.pi * s * s / 4 * h
        feats.append(([(hu - s / 2.0 - gf, 0.0, s / 2.0)], False))
    elif to == "coplanar faces":
        u0 = float(ex(inset(hu, e)))
        V += s * s * (2 * h + gf)
        feats.append(([(u0, 0.0, s / math.sqrt(2)), (u0, s, s / math.sqrt(2))], False))
    elif to == "vertex on an edge":
        V += s * s * h
        feats.append(([(hu - gf - s / math.sqrt(2), 0.0, s / math.sqrt(2))], False))
    elif to == "thin wall near tolerance":
        if gf < 0 or su == "torus":
            return None
        V -= math.pi * s * s / 4 * dd
        feats.append(([(hu - gf - s / 2.0, 0.0, s / 2.0)], False))
    elif to == "self-touching body":
        u0 = float(ex(inset(hu, e)))
        ap = s + gf
        V += (2 * math.pi * (s / 2.0) ** 2 - lens(s / 2.0, ap)) * h
        feats.append(([(u0, ap / 2.0, s / 2.0), (u0, -ap / 2.0, s / 2.0)], False))
    elif to == "multiple bodies":
        V += math.pi * s * s / 4 * h
    elif to == "edge chain around a corner":
        if shape[0] != "rect":
            return None
        V -= s * s / 2 * dd
        feats.append(([(0.0, shape[2], s / math.sqrt(2))], False))
    elif to == "feature at a seam or pole":
        return None
    # ---- order step
    d, p, dep = pf(sig(0.1 * e)), pf(sig(0.15 * e)), pf(sig(0.4 * min(H, e)))
    if orr in ("before pattern", "after pattern"):
        u = float(ex(inset(hu, e)))
        V -= 3 * math.pi * d * d / 4 * dep
        feats.append(([(-u, y, d / 2.0) for y in (-p, 0.0, p)], True))
    elif orr in ("before shell", "after shell"):
        return None
    elif orr in ("before fillet", "after fillet"):
        rim.append(("fillet", pf(sig(0.05 * e))))
    # ---- operation
    r = pf(sig(0.07 * e))
    w = pf(sig(0.05 * e))
    c = pf(sig(0.05 * e))
    top = None
    if op == "extrude":
        V += math.pi * d * d / 4 * dep
        feats.append(([(0.0, 0.0, d / 2.0)], True))
    elif op == "cut":
        V -= math.pi * d * d / 4 * dep
        feats.append(([(0.0, 0.0, d / 2.0)], True))
    elif op == "sweep":
        V += math.pi * d * d / 4 * (math.pi / 2 * 3 * d)
        feats.append(([(0.0, 0.0, d / 2.0)], True))
    elif op == "pattern (linear)":
        V -= 3 * math.pi * d * d / 4 * dep
        feats.append(([(0.0, y, d / 2.0) for y in (-p, 0.0, p)], True))
    elif op == "pattern (circular)":
        V -= 6 * math.pi * d * d / 4 * dep
        feats.append(([(0.0, 0.0, 2 * d)], True))
    elif op == "mirror":
        V += 2 * d * d * dep
        feats.append(([(0.0, 1.5 * d, d / math.sqrt(2)), (0.0, -1.5 * d, d / math.sqrt(2))], True))
    elif op == "boolean join":
        V += 2.0 / 3.0 * math.pi * d ** 3
        feats.append(([(0.0, 0.0, d)], True))
    elif op == "boolean cut":
        V -= 8 * d * d * d * (2 * math.pi / 3 + math.sqrt(3) / 4)
        feats.append(([(0.0, y * d, d) for y in (-3, -1, 1, 3)], True))
    elif op == "split":
        pass
    elif op in ("shell", "offset face", "fillet (constant)", "chamfer"):
        top = op
    else:
        return None
    # ---- surface restrictions
    if su == "torus":
        if to not in ("clean", "multiple bodies") or orr in ("before fillet", "after fillet"):
            return None
        if op not in ("cut", "pattern (linear)", "pattern (circular)", "boolean join", "split"):
            return None
    if top:
        if orr != "none" or to not in ("clean", "multiple bodies"):
            return None
        if top == "shell":
            if su == "plane":
                A, B, Hh = geo["A"], geo["B"], geo["H"]
                V = geo["V0"] - (A - 2 * w) * (B - 2 * w) * (Hh - w) if to == "clean" else None
                if V is None:
                    return None
                return V
            if su == "cylinder" and to == "clean":
                R, Hh = geo["R"], geo["H"]
                return geo["V0"] - math.pi * (R - w) ** 2 * (Hh - w)
            return None
        if top == "offset face":
            dep_o = dep
            if su == "plane":
                V += geo["A"] * geo["B"] * dep_o
            elif su == "cylinder":
                V += math.pi * geo["R"] ** 2 * dep_o
            elif su == "cone":
                R1, R2, Hh = geo["R1"], geo["R2"], geo["H"]
                R2n = R2 - dep_o * (R1 - R2) / Hh
                if R2n <= 0:
                    return None
                V += math.pi * dep_o / 3.0 * (R2 * R2 + R2 * R2n + R2n * R2n)
            else:
                return None
            return V
        if su != "cylinder" or to != "clean":
            return None
        rim.append((top, r if top == "fillet (constant)" else c))
    # ---- rim modifications (cylinder, clean, only one)
    if rim:
        if len(rim) != 1 or su != "cylinder":
            return None
        kind, rr = rim[0]
        R = geo["R"]
        for discs, _ in feats:
            for (x, y, q) in discs:
                if math.hypot(x, y) + q > R - 2.5 * 0.07 * e:
                    return None
        if kind == "chamfer":
            V -= 2 * math.pi * (R - 2 * rr / 3.0) * rr * rr
        else:
            V -= rim_fillet(R, rr)
    # ---- containment and overlap
    for discs, need in feats:
        if need:
            for dsc in discs:
                if not inside(shape, dsc):
                    return None
    for i in range(len(feats)):
        for j in range(i + 1, len(feats)):
            for (x1, y1, r1) in feats[i][0]:
                for (x2, y2, r2) in feats[j][0]:
                    if math.hypot(x1 - x2, y1 - y2) <= r1 + r2:
                        return None
    return V


# ------------------------------------------------------------------ brief
FEATURE = {"tangent edges": "boss", "coplanar faces": "pads", "vertex on an edge": "pad",
           "self-touching body": "cylinders", "thin wall near tolerance": "hole"}


def in_reach(to, b, e, g, reach):
    """True when the prelude feature stands within reach of the face's outer edge,
    so a fillet or chamfer of that edge runs into it."""
    hu = b["hu"]
    s = sig(0.12 * e)
    if to in ("tangent edges", "vertex on an edge", "thin wall near tolerance"):
        return float(g) < reach
    if to in ("coplanar faces", "self-touching body"):
        return float(ex(inset(hu, e))) + s / 2.0 > hu - reach
    return False


def brief_for(row):
    op, su, to, gp, sc, orr, pl = row
    rng = random.Random(int(hashlib.sha256(("%d:%s" % (SEED, "|".join(row))).encode()).hexdigest()[:16], 16))
    sheet = op.startswith("sheet-metal")
    b = body(su, sc, sheet, rng)
    b["su"] = su
    if su == "torus" and op in ("extrude", "sweep", "loft", "boolean cut"):
        # these steps say which way is up or down: the flat face faces -Z
        b["text"] += " (the flat face faces down, toward -Z, and the body lies above it)"
    e = min(b["Wmin"], 4 * b["H"])
    g = GAPS.get(gp)
    obj = rng.choice(OBJECTS[su]) if rng.random() < 0.6 else ""
    head = ("Like a %s: model " % obj) if obj else "Model "
    steps = [order_before(orr, b, e, op, to), prelude(to, b, e, sc, g), op_step(op, b, e, sc, to, g),
             order_after(orr, b, e, op, to)]
    hit = False
    if to in FEATURE:
        rr = None
        if op in ("fillet (constant)", "fillet (variable)", "chamfer"):
            k = {"fillet (constant)": 0.07, "fillet (variable)": 0.14, "chamfer": 0.1}[op]
            rr, idx = k * e, 2
        elif orr == "before fillet":
            rr, idx = fillet_r(op, "last", e), 3
        if rr is not None and in_reach(to, b, e, g, rr):
            hit = True
            steps[idx] += (" The %s stands within the fillet's reach, so the fillet runs into it: "
                           "round the edge up to it, or refuse if that cannot be built." % FEATURE[to]
                           ).replace("fillet", "chamfer" if op == "chamfer" else "fillet")
    steps = [x for x in steps if x]
    text = head + b["text"] + "."
    if len(steps) == 1:
        text += " " + steps[0]
    else:
        text += " " + " ".join("(%d) %s" % (i + 1, x) for i, x in enumerate(steps))
    pt = placement_text(pl)
    if pt:
        text += " " + pt
    exp = expected_for(row, g, b, e, geom(su, sc, sheet), hit)
    return obj, text, exp


# ------------------------------------------------------------------ coverage
def all_rows():
    return [r for r in itertools.product(*[AXES[k] for k in KEYS]) if feasible(r)]


def pairs_of(row):
    return [((i, row[i]), (j, row[j])) for i, j in itertools.combinations(range(len(row)), 2)]


def triple_of(row):
    return (row[IOP], row[ITO], row[IOR])


def greedy(rows, rng):
    """Three-wise over operation x topology x order, then pairwise over all axes.
    Returns (chosen row indices with their kind, #triples, #pairs)."""
    by_triple = {}
    pairs = set()
    for ri, r in enumerate(rows):
        by_triple.setdefault(triple_of(r), []).append(ri)
        pairs.update(pairs_of(r))
    uncovered = set(pairs)
    chosen = []

    def take(ri, kind):
        chosen.append((ri, kind))
        for p in pairs_of(rows[ri]):
            uncovered.discard(p)

    def gain(ri):
        return sum(1 for p in pairs_of(rows[ri]) if p in uncovered)

    triples = sorted(by_triple)
    rng.shuffle(triples)
    for t in triples:
        cands = by_triple[t]
        cands = rng.sample(cands, min(60, len(cands)))
        take(max(cands, key=gain), "threewise")
    # pairwise over everything
    idx = {r: n for n, r in enumerate(rows)}
    while uncovered:
        target = rng.choice(sorted(uncovered))
        (i, a), (j, bb) = target
        cands = []
        for _ in range(4000):
            r = [rng.choice(AXES[k]) for k in KEYS]
            r[i], r[j] = a, bb
            r = tuple(r)
            if feasible(r):
                cands.append(r)
                if len(cands) >= 60:
                    break
        if not cands:
            cands = [r for r in itertools.product(*[([a] if k == i else [bb] if k == j else AXES[KEYS[k]])
                                                    for k in range(len(KEYS))]) if feasible(r)]
            cands = rng.sample(cands, min(60, len(cands)))
        take(max((idx[r] for r in cands), key=gain), "pairwise")
    return chosen, len(by_triple), len(pairs)


def main(out="items.jsonl"):
    rng = random.Random(SEED)
    rows = all_rows()
    chosen, ntrip, npairs = greedy(rows, rng)
    kinds = {}
    seen_rows = set()
    first = []
    for ri, k in chosen:
        if ri not in seen_rows:
            seen_rows.add(ri)
            first.append(ri)
            kinds[ri] = k
    nthree = sum(1 for ri in first if kinds[ri] == "threewise")
    print("feasible rows:", len(rows), "triples:", ntrip, "pairs:", npairs,
          "threewise items:", nthree, "pairwise items:", len(first) - nthree, file=sys.stderr)
    # random fill, weighted on placement
    rest_idx = [i for i in range(len(rows)) if i not in seen_rows]
    weights = [PLACEMENT_WEIGHT[rows[i][IPL]] / (len(GAPS) if rows[i][ITO] in CONTACT else 1) for i in rest_idx]
    draw = rng.choices(rest_idx, weights=weights, k=3 * COUNT)
    order = first[:]
    taken = set(first)
    for i in draw:
        if len(order) >= COUNT:
            break
        if i not in taken:
            taken.add(i)
            order.append(i)
    if len(order) < COUNT:
        sys.exit("not enough feasible rows: %d" % len(order))
    order = order[:COUNT]
    rng.shuffle(order)      # covering items spread over every batch
    seen = set()
    n = 0
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for ri in order:
            r = rows[ri]
            obj, brief, exp = brief_for(r)
            if brief in seen:
                continue
            seen.add(brief)
            n += 1
            kind = kinds.get(ri, "random")
            item = {"id": "S%04d" % n, "operation": r[IOP], "surface": r[ISU], "topology": r[ITO],
                    "gap": r[IGAP], "scale": r[ISC], "order": r[IOR], "placement": r[IPL],
                    "object": obj, "coverage": kind, "pairwise": kind != "random",
                    "brief": brief, "expected": exp}
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            if n == COUNT:
                break
    if n < COUNT:
        sys.exit("only %d unique items" % n)
    print("wrote", n, "items to", out, file=sys.stderr)


HEADER = (
    "Claim this batch by assigning it to yourself (or comment \"claiming\" and a maintainer will assign you). "
    "Tick each item as you finish it and close the issue when all are ticked. "
    "Results go in `results/batch-%(nn)s.jsonl`; put each failure and its signature in the results, see the README.\n\n"
    "Placement: an item that ends in [rotated], [far] or [rotated + far] has one last step, applied to the whole finished part. "
    "Rotated: %(rot)s. Far: move everything %(far)d mm along +X, so the original origin lands at (%(far)d, 0, 0). "
    "Rotated + far: rotate first, then move.\n\n"
)


def batch_body(items, n):
    xi, zi = rot_axes()
    rot = ("rotate everything %d\u00b0 about X, then %d\u00b0 about Z, both through the origin (right-hand rule); "
           "the original X axis then points along %s and the original Z axis along %s" % (ROT_X, ROT_Z, vec(xi), vec(zi)))
    out = HEADER % {"nn": "%02d" % n, "rot": rot, "far": FAR_MM}
    lines = []
    for it in items:
        brief = it["brief"]
        if it["placement"] != "origin":
            brief = brief.rsplit(" Finally, ", 1)[0] + " [%s]" % it["placement"]
        lines.append("- [ ] %s \u00b7 %s" % (it["id"], brief))
    return out + "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "batch":
        # python3 gen.py batch N [items.jsonl]: print the body of batch issue N (1-100)
        n = int(sys.argv[2])
        with open(sys.argv[3] if len(sys.argv) > 3 else "items.jsonl", encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh]
        sys.stdout.write(batch_body(rows[(n - 1) * 100:n * 100], n))
    else:
        main(*sys.argv[1:2])
