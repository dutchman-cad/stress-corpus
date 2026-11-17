"""Shared helpers for the results tools."""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTCOMES = {"correct", "wrong-result", "correct-refusal", "wrong-refusal",
            "crash", "hang", "skipped"}
PIPELINES = {"solidcraft", "fusion-dutchman"}
# Outcomes that count as a failure to be grouped and filed.
FAILURES = {"wrong-result", "wrong-refusal", "crash", "hang"}
REL_TOL = 1e-6


def load_items(path=None):
    path = path or os.path.join(ROOT, "items.jsonl")
    items = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                it = json.loads(line)
                items[it["id"]] = it
    return items


def result_files(paths=None):
    if paths:
        return list(paths)
    return sorted(glob.glob(os.path.join(ROOT, "results", "*.jsonl")))


def volume_matches(measured, expected):
    scale = max(abs(expected), 1e-12)
    return abs(measured - expected) / scale <= REL_TOL
