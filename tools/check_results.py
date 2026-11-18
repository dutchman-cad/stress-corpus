#!/usr/bin/env python3
"""Check results files before they are merged.

Usage: python3 tools/check_results.py [results/batch-07.jsonl ...]
With no arguments it checks every file in results/. Exit status 1 on any error.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (OUTCOMES, PIPELINES, load_items, result_files,  # noqa: E402
                    volume_matches)

COUNT_KEYS = ("bodies", "faces", "edges", "vertices")


def check_line(rec, items, where):
    """Return (errors, warnings) for one parsed results line."""
    errs, warns = [], []
    if not isinstance(rec, dict):
        return [f"{where}: the line is not a JSON object"], warns
    iid = rec.get("id")
    item = items.get(iid)
    if item is None:
        errs.append(f"{where}: id {iid!r} is not in items.jsonl")
    pipeline = rec.get("pipeline")
    if pipeline not in PIPELINES:
        errs.append(f"{where}: pipeline {pipeline!r} is unknown; use one of "
                    + ", ".join(sorted(PIPELINES)))
    outcome = rec.get("outcome")
    if outcome not in OUTCOMES:
        errs.append(f"{where}: outcome {outcome!r} is not one of "
                    + ", ".join(sorted(OUTCOMES)))
    if not isinstance(rec.get("version"), str) or not rec.get("version"):
        errs.append(f"{where}: \"version\" is missing; say which SolidCraft or Dutchman release you used")
    for key in ("signature", "build", "link", "rerun_of"):
        if key in rec and not isinstance(rec[key], str):
            errs.append(f"{where}: \"{key}\" must be a string")
    if "rerun_of" in rec and not (isinstance(rec["rerun_of"], str) and rec["rerun_of"].strip()):
        errs.append(f"{where}: \"rerun_of\" must be a non-empty string, the earlier build this line re-checks")
    # A skipped item has no failure to describe, and a failure already filed upstream is described by its link.
    linked = isinstance(rec.get("link"), str) and rec["link"].startswith("https://")
    if outcome in OUTCOMES and outcome not in ("correct", "skipped", "correct-refusal") and not linked:
        sig = rec.get("signature")
        if not isinstance(sig, str) or not sig.strip():
            errs.append(f"{where}: outcome {outcome!r} needs a \"signature\": the first error or "
                        "refusal message, then \" @ \" and the failing operation or command")
    if outcome == "source-failed" and pipeline != "freecad-dutchman":
        errs.append(f"{where}: source-failed is only valid for freecad-dutchman")
    measured = rec.get("measured")
    if measured is not None:
        if not isinstance(measured, dict):
            errs.append(f"{where}: \"measured\" must be an object")
            measured = None
        else:
            for k, v in measured.items():
                if k != "volume_mm3" and k not in COUNT_KEYS:
                    warns.append(f"{where}: measured has an unknown field {k!r}")
                elif isinstance(v, bool) or not isinstance(v, (int, float)):
                    errs.append(f"{where}: measured.{k} must be a number")
                    measured = None
                    break
    if item and measured is not None and outcome in ("correct", "wrong-result"):
        exp = item.get("expected", {})
        vol_ok = body_ok = None
        if "volume_mm3" in exp and "volume_mm3" in measured:
            vol_ok = volume_matches(measured["volume_mm3"], exp["volume_mm3"])
        if "bodies" in exp and "bodies" in measured:
            body_ok = measured["bodies"] == exp["bodies"]
        if outcome == "correct":
            if vol_ok is False:
                errs.append(f"{where}: {iid} is marked correct but the measured volume "
                            f"{measured['volume_mm3']} differs from the expected "
                            f"{exp['volume_mm3']} by more than 1e-6 relative; use wrong-result")
            if body_ok is False:
                errs.append(f"{where}: {iid} is marked correct but it has {measured['bodies']} "
                            f"bodies and {exp['bodies']} are expected; use wrong-result")
        else:
            if vol_ok is True and body_ok is not False:
                errs.append(f"{where}: {iid} is marked wrong-result but the measured volume matches "
                            "the expected one and the body count is not different; say what is "
                            "wrong in the signature and measure the faces, or mark it correct")
    if (item and outcome == "correct" and "volume_mm3" in item.get("expected", {})
            and not (measured and "volume_mm3" in measured)):
        warns.append(f"{where}: {iid} has an expected volume but this correct result has no "
                     "measured.volume_mm3")
    return errs, warns


def check_file(path, items):
    errs, warns, seen = [], [], set()
    name = os.path.relpath(path)
    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        return [f"{name}: cannot read the file ({e.strerror})"], warns
    with f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            where = f"{name}:{n}"
            try:
                rec = json.loads(line)
            except ValueError as e:
                errs.append(f"{where}: not valid JSON ({e})")
                continue
            e, w = check_line(rec, items, where)
            errs += e
            warns += w
            if isinstance(rec, dict):
                # A line with rerun_of re-checks an earlier line on a newer build and sits beside it
                # (README, "Check again on a newer build"): one per earlier build is allowed.
                key = (rec.get("id"), rec.get("pipeline"), rec.get("rerun_of") or "")
                if key in seen:
                    errs.append(f"{where}: {key[0]} appears twice for pipeline {key[1]}"
                                + (f" re-checking {key[2]}" if key[2] else ""))
                seen.add(key)
    return errs, warns


def main(argv):
    items = load_items()
    files = result_files(argv)
    errs, warns = [], []
    for p in files:
        e, w = check_file(p, items)
        errs += e
        warns += w
    for w in warns:
        print("warning:", w)
    for e in errs:
        print("error:", e)
    print(f"checked {len(files)} file(s): {len(errs)} error(s), {len(warns)} warning(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
