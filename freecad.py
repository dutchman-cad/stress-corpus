#!/usr/bin/env python3
"""Generate parametric FreeCAD macros, then exercise FreeCAD → Dutchman → SolidCraft.

Geometry numbers and step ordering come from gen via scripts.script_for. No FreeCAD
implementation sources are used. See README for the documented API references.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

import gen
import scripts
from tools.common import volume_matches

Unsupported = scripts.Unsupported

# This source is embedded in every macro so it also runs independently of this repo.
RUNTIME = r'''
import FreeCAD as App
import Part
import Sketcher
import math
import json
import os
import traceback
V = App.Vector
doc = App.newDocument("Corpus")
bodies = {}
named = {}
body = None
last = None
sk = None
step = "initialise"

def new_body(name):
    global body, last
    body = doc.addObject("PartDesign::Body", name)
    bodies[name] = body
    last = None

def sketch(origin=(0,0,0), x=(1,0,0), y=(0,1,0), name="Sketch"):
    s = body.newObject("Sketcher::SketchObject", name)
    vx, vy = V(*x), V(*y)
    s.Placement = App.Placement(V(*origin), App.Rotation(vx, vy, vx.cross(vy), "ZXY"))
    return s

def polygon(s, pts, rectangle=False):
    ids = []
    for a,b in zip(pts, pts[1:]+pts[:1]):
        ids.append(s.addGeometry(Part.LineSegment(V(*a,0), V(*b,0)),False))
    if rectangle:
        for i in range(4):
            s.addConstraint(Sketcher.Constraint("Coincident",ids[i],2,ids[(i+1)%4],1))
            s.addConstraint(Sketcher.Constraint("Horizontal" if i%2==0 else "Vertical",ids[i]))
        s.addConstraint(Sketcher.Constraint("Distance",ids[0],abs(pts[1][0]-pts[0][0])))
        s.addConstraint(Sketcher.Constraint("Distance",ids[1],abs(pts[2][1]-pts[1][1])))
        s.addConstraint(Sketcher.Constraint("DistanceX",ids[0],1,pts[0][0]))
        s.addConstraint(Sketcher.Constraint("DistanceY",ids[0],1,pts[0][1]))
    else:
        for i in ids:
            for point,xy in ((1,pts[ids.index(i)]),(2,pts[(ids.index(i)+1)%len(pts)])):
                s.addConstraint(Sketcher.Constraint("DistanceX",i,point,xy[0]))
                s.addConstraint(Sketcher.Constraint("DistanceY",i,point,xy[1]))

def rect(s,a,b):
    polygon(s,[a,[b[0],a[1]],b,[a[0],b[1]]],True)

def circle(s,c,r):
    i = s.addGeometry(Part.Circle(V(*c,0),V(0,0,1),r),False)
    s.addConstraint(Sketcher.Constraint("Radius",i,r))
    if c == [0,0] or c == (0,0):
        s.addConstraint(Sketcher.Constraint("Coincident",i,3,-1,1))
    else:
        s.addConstraint(Sketcher.Constraint("DistanceX",i,3,c[0]))
        s.addConstraint(Sketcher.Constraint("DistanceY",i,3,c[1]))

def finish(f):
    global last
    doc.recompute()
    if f.Shape.isNull() or not f.Shape.isValid() or 'Invalid' in f.State:
        raise RuntimeError(f.Name + ": " + str(f.State) + " " + str(f.getStatusString()))
    if last: last.Visibility = False
    last = f
    body.Tip = f
    return f

def extrusion(s,length,cut=False,reverse=False,name=None):
    f = body.newObject("PartDesign::Pocket" if cut else "PartDesign::Pad", name or ("Pocket" if cut else "Pad"))
    f.Profile = s
    f.Length = length
    f.Reversed = reverse
    finish(f)
    s.Visibility = False
    return f

def revolve(s,cut=False):
    f = body.newObject("PartDesign::Groove" if cut else "PartDesign::Revolution","Groove" if cut else "Revolution")
    f.Profile = s
    f.ReferenceAxis = (s,["V_Axis"])
    f.Angle = 360
    finish(f)
    s.Visibility = False
    return f

def picks(points,kind):
    shape = last.Shape
    seq = shape.Edges if kind == "Edge" else shape.Faces
    names = []
    for p in points:
        distances = [g.distToShape(Part.Vertex(V(*p)))[0] for g in seq]
        j = min(range(len(seq)),key=lambda i:distances[i])
        if distances[j] > max(1e-6,shape.BoundBox.DiagonalLength*1e-8):
            raise RuntimeError("no " + kind + " at " + str(p))
        name = kind + str(j+1)
        if name not in names: names.append(name)
    return names

def primitive(cmd,p):
    global body,last
    op = p.get("operation","new")
    target = bodies.get(p.get("targets",["Part"])[0])
    boolean = (op == "intersect" or (cmd == "solid.sphere" and op != "new")
               or (cmd == "solid.cylinder" and p.get("axis") == "Y" and op == "cut"))
    if op == "new" or boolean:
        new_body(p.get("body_name","Tool"))
    elif target:
        body = target
        last = body.Tip
    cut = op == "cut" and not boolean
    if cmd == "solid.box":
        x,y,z = p["corner"]
        s = sketch((0,0,z))
        rect(s,[x,y],[x+p["length"],y+p["width"]])
        # Pocket runs into the sketch; this box extends above its corner.
        extrusion(s,p["height"],cut,cut,p.get("name"))
    elif cmd == "solid.cylinder":
        axis = p.get("axis","Z")
        s = sketch(p["base"],(1,0,0),(0,0,-1) if axis == "Y" else (0,1,0))
        circle(s,[0,0],p["radius"])
        extrusion(s,p["height"],cut,cut,p.get("name"))
    elif cmd == "solid.cone":
        s = sketch(p["base"],(1,0,0),(0,0,1))
        polygon(s,[[0,0],[p["radius"],0],[p["top_radius"],p["height"]],[0,p["height"]]])
        revolve(s)
    elif cmd == "solid.sphere":
        r = p["radius"]
        s = sketch(p["center"],(1,0,0),(0,0,1))
        i = s.addGeometry(Part.Arc(V(0,-r,0),V(r,0,0),V(0,r,0)),False)
        s.addConstraint(Sketcher.Constraint("Radius",i,r))
        s.addConstraint(Sketcher.Constraint("Coincident",i,3,-1,1))
        s.addConstraint(Sketcher.Constraint("PointOnObject",i,1,-2))
        s.addConstraint(Sketcher.Constraint("PointOnObject",i,2,-2))
        j = s.addGeometry(Part.LineSegment(V(0,r,0),V(0,-r,0)),False)
        s.addConstraint(Sketcher.Constraint("Coincident",j,1,i,2))
        s.addConstraint(Sketcher.Constraint("Coincident",j,2,i,1))
        revolve(s)
    elif cmd == "solid.torus":
        s = sketch(p["center"],(1,0,0),(0,0,1))
        circle(s,[p["major"],0],p["minor"])
        revolve(s)
    if p.get("name"): named[p["name"]] = last
    if boolean:
        tool = body
        body = target
        last = body.Tip
        f = body.newObject("PartDesign::Boolean","Boolean")
        f.Group = [tool]
        f.Type = {"join":0,"cut":1,"intersect":2}[op]
        finish(f)
        bodies.pop(tool.Name,None)
        tool.Visibility = False

def execute(cmd,p):
    global body,last,sk
    if cmd == "sketch.create" and body is None:
        new_body("Part")
    if cmd in ("sketch.create","solid.fillet","solid.chamfer","solid.shell","solid.draft","solid.mirror") and "Part" in bodies:
        body = bodies["Part"]
        last = body.Tip
    if cmd in ("solid.box","solid.cylinder","solid.cone","solid.sphere","solid.torus"):
        primitive(cmd,p)
    elif cmd == "sketch.create":
        q = p["plane"]
        sk = sketch(q["origin"],q["x_dir"],q["y_dir"],p["name"])
        named[p["name"]] = sk
    elif cmd == "sketch.rectangle.two_point": rect(sk,p["p0"],p["p1"])
    elif cmd == "sketch.circle.center": circle(sk,p["center"],p["radius"])
    elif cmd == "sketch.spline.fit_point":
        curve = Part.BSplineCurve()
        curve.interpolate([V(*q,0) for q in p["points"]],PeriodicFlag=True)
        i = sk.addGeometry(curve,False)
        sk.addConstraint(Sketcher.Constraint("Block",i))
    elif cmd == "sketch.arc.center_point":
        c,a,b = p["center"],p["start"],p["end"]
        t0 = math.atan2(a[1]-c[1],a[0]-c[0])
        t1 = math.atan2(b[1]-c[1],b[0]-c[0])
        while t1 <= t0: t1 += 2*math.pi
        r = math.dist(c,a)
        mid = V(c[0]+r*math.cos((t0+t1)/2),c[1]+r*math.sin((t0+t1)/2),0)
        i = sk.addGeometry(Part.Arc(V(*a,0),mid,V(*b,0)),False)
        sk.addConstraint(Sketcher.Constraint("Block",i))
    elif cmd == "sketch.finish": doc.recompute()
    elif cmd == "solid.extrude":
        extrusion(named[p["sketch"]],p["distance"],p["operation"] == "cut")
    elif cmd == "solid.revolve": revolve(named[p["sketch"]],p["operation"] == "cut")
    elif cmd in ("solid.fillet","solid.chamfer"):
        edges = picks(p["edges"],"Edge")
        f = body.newObject("PartDesign::Fillet" if cmd == "solid.fillet" else "PartDesign::Chamfer","Blend")
        f.Base = (last,edges)
        if cmd == "solid.fillet": f.Radius = p["radius"]
        else:
            f.ChamferType = "Two distances"
            f.Size = p["distance"]
            f.Size2 = p["distance2"]
        finish(f)
    elif cmd == "solid.shell":
        faces = picks(p["faces"],"Face")
        f = body.newObject("PartDesign::Thickness","Thickness")
        f.Base = (last,faces)
        f.Value = p["thickness"]
        f.Reversed = True
        finish(f)
    elif cmd == "solid.draft":
        faces = picks(p["faces"],"Face")
        f = body.newObject("PartDesign::Draft","Draft")
        f.Base = (last,faces)
        f.Angle = float(p["angle"].split()[0])
        f.NeutralPlane = (doc.getObject("XY_Plane"),[""])
        f.PullDirection = (doc.getObject("Z_Axis"),[""])
        finish(f)
    elif cmd.startswith("solid.pattern."):
        feature = named[p["features"][0]]
        circular = cmd.endswith("circular")
        f = body.newObject("PartDesign::PolarPattern" if circular else "PartDesign::LinearPattern","Pattern")
        f.Originals = [feature]
        if circular:
            f.Axis = (doc.getObject("Z_Axis"),[""])
            f.Angle = 360
            f.Occurrences = p["count"]
        else:
            f.Direction = (doc.getObject("Y_Axis"),[""])
            f.Length = (p["count1"]-1)*p["spacing1"]
            f.Occurrences = p["count1"]
        finish(f)
    elif cmd == "solid.mirror":
        f = body.newObject("PartDesign::Mirrored","Mirror")
        f.Originals = [named[p["features"][0]]]
        f.MirrorPlane = (doc.getObject("XZ_Plane"),[""])
        finish(f)
    elif cmd == "solid.sweep":
        f = body.newObject("PartDesign::AdditivePipe","Sweep")
        f.Profile = named[p["sketch"]]
        f.Spine = (named[p["path_sketch"]],["Edge1"])
        finish(f)
        named[p["sketch"]].Visibility = False
        named[p["path_sketch"]].Visibility = False
    elif cmd == "solid.loft":
        sections = [named[x["sketch"]] for x in p["sections"]]
        f = body.newObject("PartDesign::AdditiveLoft","Loft")
        f.Profile = sections[0]
        f.Sections = sections[1:]
        finish(f)
        for s in sections: s.Visibility = False
    elif cmd == "solid.combine":
        body = bodies[p["target"]]
        last = body.Tip
        tools = [bodies[x] for x in p["tools"]]
        f = body.newObject("PartDesign::Boolean","Boolean")
        f.Group = tools
        f.Type = {"join":0,"cut":1,"intersect":2}[p["operation"]]
        finish(f)
        for name in p["tools"]:
            bodies.pop(name).Visibility = False
    elif cmd == "solid.move":
        for name in p["bodies"]:
            b = bodies[name]
            if "translate" in p: t = App.Placement(V(*p["translate"]),App.Rotation())
            else:
                rotation = App.Rotation(V(*p["axis"]),float(p["angle"].split()[0]))
                origin = V(*p["origin"])
                t = App.Placement(origin-rotation.multVec(origin),rotation)
            b.Placement = t.multiply(b.Placement)
        doc.recompute()
    else: raise RuntimeError("unimplemented command " + cmd)
'''

SUPPORTED = {
    "solid.box", "solid.cylinder", "solid.cone", "solid.sphere", "solid.torus",
    "sketch.create", "sketch.rectangle.two_point", "sketch.circle.center", "sketch.finish",
    "sketch.spline.fit_point", "sketch.arc.center_point", "solid.mirror", "solid.sweep",
    "solid.extrude", "solid.revolve", "solid.fillet", "solid.chamfer", "solid.shell", "solid.draft",
    "solid.pattern.rectangular", "solid.pattern.circular", "solid.loft", "solid.combine", "solid.move",
}


def macro_for(item):
    """Return a standalone macro, or name the first step without a faithful mapping."""
    spec = scripts.script_for(item)
    for c in spec["commands"]:
        cmd, p = c["command"], c["params"]
        if cmd not in SUPPORTED:
            raise Unsupported(cmd + ": no PartDesign mapping yet")
        if cmd == "solid.fillet" and p.get("type") == "variable":
            raise Unsupported("solid.fillet: variable radius along a loop")
        if cmd == "solid.shell" and not p["faces"]:
            raise Unsupported("solid.shell: closed shell without removed faces")
        if cmd == "solid.pattern.circular" and p["axis"] != "Z":
            raise Unsupported("solid.pattern.circular: offset axis")
    # json is stored as a Python string, avoiding JSON's null/true in Python code.
    source = RUNTIME + '\ncommands = json.loads(' + repr(json.dumps(spec["commands"])) + ')\n'
    source += '''try:
    for command in commands:
        step = command["command"]
        execute(step,command["params"])
    step = "save/export"
    doc.recompute()
    for b in bodies.values():
        if b.Tip is None or b.Shape.isNull() or not b.Shape.isValid():
            raise RuntimeError("invalid final body " + b.Name)
    doc.saveAs(os.path.abspath(ITEM_ID + ".FCStd"))
    Part.export(list(bodies.values()),os.path.abspath(ITEM_ID + ".step"))
    with open(ITEM_ID + ".built.json","w") as f:
        json.dump({"version":"FreeCAD " + ".".join(App.Version()[:3]),
                   "bodies":sum(len(b.Shape.Solids) for b in bodies.values()),
                   "volume_mm3":sum(b.Shape.Volume for b in bodies.values())},f)
except Exception as e:
    with open(ITEM_ID + ".error.txt","w") as f:
        f.write(str(e) + " @ " + step)
    traceback.print_exc()
finally:
    App.closeDocument(doc.Name)
'''
    return 'ITEM_ID = ' + repr(item['id']) + '\n' + source


def record(item):
    try:
        return dict(id=item["id"], macro=macro_for(item), unsupported=None, expected=item["expected"])
    except Unsupported as why:
        return dict(id=item["id"], macro=None, unsupported=str(why), expected=item["expected"])


def write(path, chosen):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    n = ok = 0
    with open(path, "w", encoding="utf-8") as f:
        for item in chosen:
            rec = record(item)
            f.write(json.dumps(rec) + "\n")
            n += 1
            ok += rec["macro"] is not None
    print(f"wrote {n} items to {path}: {ok} macros, {n-ok} unsupported", file=sys.stderr)


def process(args, cwd, timeout):
    """Bound the whole process group, including children of AppImage launchers."""
    p = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise
    return p.returncode, out, err


def history_counts(data):
    """Dutchman's JSON history summary (kept separate from geometry measurements)."""
    if isinstance(data, dict):
        for key in ("history", "history_mapping"):
            h = data.get(key)
            if isinstance(h, dict) and "mapped" in h and "total" in h:
                return {"mapped": h["mapped"], "total": h["total"]}
        for v in data.values():
            found = history_counts(v)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = history_counts(v)
            if found is not None:
                return found
    return None


def compare(expected, measured):
    if measured.get("bodies") != expected.get("bodies"):
        return False
    want = expected.get("volume_mm3")
    return want is None or ("volume_mm3" in measured and volume_matches(measured["volume_mm3"], want))


def run_one(rec, args, versions, root):
    result = dict(id=rec["id"], pipeline="freecad-dutchman", version="; ".join(versions.values()),
                  versions=dict(versions), build=versions["dutchman"], link="")
    if not rec["macro"]:
        return dict(result, outcome="skipped", signature=rec["unsupported"])
    work = Path(root) / rec["id"]
    work.mkdir()
    iid = rec["id"]
    macro = work / (iid + ".py")
    macro.write_text(rec["macro"], encoding="utf-8")
    stage = "FreeCAD build"
    try:
        code, out, err = process([args.freecad, str(macro)], work, args.timeout)
        built = work / (iid + ".built.json")
        if code or not built.exists() or not all((work / (iid + ext)).exists() for ext in (".FCStd", ".step")):
            error = work / (iid + ".error.txt")
            sig = error.read_text() if error.exists() else (scripts.first_error(err + out) or f"exit {code}") + " @ " + stage
            return dict(result, outcome="source-failed", signature=sig)
        source = json.loads(built.read_text())
        result["versions"]["freecad"] = source.pop("version")
        result["version"] = "; ".join(result["versions"].values())
        result["source_measured"] = source
        if not compare(rec["expected"], source):
            return dict(result, outcome="source-failed",
                        signature=f"source measured {source}; expected {rec['expected']} @ FreeCAD build")
        stage = "dutchman convert"
        code, out, err = process([args.dutchman, "convert", str(work / (iid + ".FCStd")),
                                  str(work / (iid + ".sc")), "--json"], work, args.timeout)
        try:
            data = json.loads(out)
        except ValueError:
            data = None
        result["history"] = history_counts(data)
        result["conversion"] = {k: v for k, v in data.items() if k not in ("names", "history")} if data else None
        if code:
            return failed(result, code, err + out, rec["expected"], stage)
        stage = "solidcraft-cli run"
        code, out, err = process([args.cli, "run", str(work / (iid + ".sc")), "--param-check", "strict"], work, args.timeout)
        if code:
            return failed(result, code, err + out, rec["expected"], stage)
        got = scripts.measured(out)
        if not got or got[0] is None or got[1] is None:
            return dict(result, outcome="crash", signature="built, but printed no summary @ " + stage)
        meas = got[2]
        if not compare(rec["expected"], meas):
            return dict(result, outcome="wrong-result", measured=meas,
                        signature=f"measured {meas}; expected {rec['expected']} @ result")
        return dict(result, outcome="correct", measured=meas,
                    detail="volume and body count" if "volume_mm3" in rec["expected"] else "body count only; geometry not inspected")
    except subprocess.TimeoutExpired:
        return dict(result, outcome="source-failed" if stage == "FreeCAD build" else "hang",
                    signature=f"no result within {args.timeout} s @ {stage}")
    except OSError as e:
        return dict(result, outcome="source-failed" if stage == "FreeCAD build" else "crash", signature=str(e) + " @ " + stage)


def failed(result, code, text, expected, stage):
    outcome = "crash" if code < 0 or "panick" in text.lower() else (
        "correct-refusal" if expected.get("refusal_ok") else "wrong-refusal")
    return dict(result, outcome=outcome, signature=(scripts.first_error(text) or f"exit {code}") + " @ " + stage)


def run(args):
    versions = {"freecad": scripts.cli_version(args.freecad) or "FreeCAD unknown",
                "dutchman": scripts.cli_version(args.dutchman) or "Dutchman unknown",
                "solidcraft": scripts.cli_version(args.cli) or "SolidCraft unknown"}
    recs = [json.loads(l) for l in Path(args.run).read_text().splitlines() if l.strip()]
    path = Path(args.results or "fc-check.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    done, counts = [], {}
    with tempfile.TemporaryDirectory(prefix="fc-corpus-") as tmp, \
            ThreadPoolExecutor(max_workers=args.jobs) as pool, path.open("w", encoding="utf-8") as f:
        futures = [pool.submit(run_one, r, args, versions, tmp) for r in recs]
        for future in as_completed(futures):
            res = future.result()
            done.append(res)
            counts[res["outcome"]] = counts.get(res["outcome"], 0) + 1
            f.write(json.dumps(res) + "\n")
            f.flush()
            print(res["id"], res["outcome"], file=sys.stderr)
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(done,key=lambda r:r["id"])), encoding="utf-8")
    print(f"checked {len(done)} items: {counts}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--batch", type=int, choices=range(1,101), metavar="N")
    mode.add_argument("--run", metavar="FILE")
    parser.add_argument("--freecad")
    parser.add_argument("--dutchman")
    parser.add_argument("--cli")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--results")
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.timeout <= 0:
        parser.error("--jobs and --timeout must be positive")
    if args.run:
        if not all((args.freecad,args.dutchman,args.cli)):
            parser.error("--run needs --freecad, --dutchman and --cli")
        for key in ("freecad","dutchman","cli"):
            value = os.path.expanduser(getattr(args,key))
            if os.path.sep in value: value = os.path.abspath(value)
            setattr(args,key,value)
        run(args)
    else:
        chosen = scripts.items()
        if args.batch:
            chosen = (it for it in chosen if 100*(args.batch-1) < int(it["id"][1:]) <= 100*args.batch)
        write(f"freecad/batch-{args.batch:02d}.jsonl" if args.batch else "freecad.jsonl", chosen)


if __name__ == "__main__":
    main()
