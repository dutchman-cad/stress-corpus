#!/usr/bin/env python3
"""Group failures from results/*.jsonl by cause and print one issue draft per cause.

Usage: python3 tools/cluster.py [--known known.json] [--results DIR_OR_FILE ...]

Grouping key: pipeline, outcome kind, and the normalised signature. A signature is
"<first message> @ <failing operation or command>"; numbers, ids, hex values and
paths are stripped so the same cause written with different sizes lands together.
known.json maps a signature key (as printed in each draft) to the upstream issue URL.
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FAILURES, ROOT, load_items, result_files  # noqa: E402

AXES = ("operation", "topology", "gap", "placement")
REPOS = {"solidcraft": "tangency-labs/solidcraft", "fusion-dutchman": "dutchman-cad/dutchman"}

_PATH = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\s\\/:'\"]+){2,}")
_ITEM = re.compile(r"\bS\d{3,5}\b")
_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}\b")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")
_NUM = re.compile(r"[-+]?\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b|[-+]?\.\d+")


def normalise(sig):
    s = sig.strip()
    s = _UUID.sub("<id>", s)
    s = _PATH.sub("<path>", s)
    s = _ITEM.sub("<item>", s)
    s = _HEX.sub("<id>", s)
    s = _NUM.sub("<n>", s)
    s = re.sub(r"(?<=[A-Za-z_])\d+\b", "<n>", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def display(sig):
    """Normalised signature for a title: paths become <file>, numbers become N."""
    return normalise(sig).replace("<path>", "<file>").replace("<n>", "N").replace("<id>", "N").replace("<item>", "N")


def strip_paths(text):
    return _PATH.sub("<file>", text)


def kind(outcome):
    return "wrong result" if outcome == "wrong-result" else outcome


def feature_count(item):
    return len(re.findall(r"\(\d+\)", item["brief"]))


def smallest(items_):
    return min(items_, key=lambda it: (len(it["brief"]), feature_count(it), it["id"]))


def load_failures(files, items):
    out = []
    for p in files:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("outcome") in FAILURES and r.get("id") in items and r.get("signature"):
                    out.append(r)
    return out


def cluster(failures):
    groups = collections.OrderedDict()
    for r in failures:
        key = " | ".join((r.get("pipeline", ""), kind(r["outcome"]), normalise(r["signature"])))
        groups.setdefault(key, []).append(r)
    return groups


def draft(key, recs, items):
    pipeline, outcome = key.split(" | ")[:2]
    members = {}
    for r in recs:
        members.setdefault(r["id"], items[r["id"]])
    rep = smallest(list(members.values()))
    ids = sorted(members)
    sample = next(r for r in recs if r["id"] == rep["id"])["signature"]
    norm = display(sample)
    msg, _, op = norm.partition(" @ ")
    op = op or rep["operation"]
    lines = [f"Title: {outcome} in {op}: {msg[:80]} ({rep['id']})",
             f"Repo: {REPOS.get(pipeline, pipeline)}",
             f"Key: {key}",
             f"Example message: {strip_paths(sample)}", "",
             f"### Smallest failing item: {rep['id']}", "", rep["brief"], "",
             f"Expected: {json.dumps(rep['expected'])}", "",
             "### Items it covers", "", f"{len(ids)} item(s): " + ", ".join(ids), "",
             "### Boundary: what every member shares", ""]
    shared = 0
    for ax in AXES:
        vals = {members[i][ax] for i in ids}
        if len(vals) == 1:
            lines.append(f"- {ax}: {next(iter(vals))}")
            shared += 1
        else:
            lines.append(f"- {ax}: varies ({', '.join(sorted(vals))})")
    if not shared:
        lines.append("- no single axis value is common to all members")
    lines += ["", "### Counts by version", ""]
    counts = collections.Counter(r.get("build") or r.get("version", "unknown") for r in recs)
    for v, c in sorted(counts.items()):
        lines.append(f"- {v}: {c}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--known", help="JSON file mapping signature key to upstream issue URL")
    ap.add_argument("results", nargs="*", help="results files (default: results/*.jsonl)")
    a = ap.parse_args(argv)
    known = {}
    if a.known:
        with open(a.known, encoding="utf-8") as f:
            known = json.load(f)
    items = load_items()
    groups = cluster(load_failures(result_files(a.results), items))
    shown = skipped = 0
    for key, recs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if key in known:
            skipped += 1
            continue
        if shown:
            print("\n---\n")
        print(draft(key, recs, items))
        shown += 1
    print(f"\n{shown} cluster(s) to file, {skipped} already filed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
