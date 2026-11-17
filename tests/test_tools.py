"""Run with: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import check_results  # noqa: E402
import cluster  # noqa: E402
from common import load_items  # noqa: E402

ITEMS = load_items()
GOOD = os.path.join(HERE, "good.jsonl")
BAD = os.path.join(HERE, "bad.jsonl")


class CheckResults(unittest.TestCase):
    def test_good_file_passes(self):
        errs, _ = check_results.check_file(GOOD, ITEMS)
        self.assertEqual(errs, [])

    def test_bad_file_reports_each_problem(self):
        errs, _ = check_results.check_file(BAD, ITEMS)
        text = "\n".join(errs)
        for needle in ("not in items.jsonl", "pipeline 'blender'", "outcome 'fine'",
                       "needs a \"signature\"", "marked correct but the measured volume",
                       "marked wrong-result but the measured volume matches"):
            self.assertIn(needle, text)
        self.assertEqual(len(errs), 6)

    def test_duplicate_and_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.jsonl")
            with open(GOOD) as g:
                line = g.readline()
            with open(p, "w") as w:
                w.write(line + line + "{oops\n")
            errs, _ = check_results.check_file(p, ITEMS)
        self.assertTrue(any("appears twice" in e for e in errs))
        self.assertTrue(any("not valid JSON" in e for e in errs))

    def test_main_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_results.main([GOOD]), 0)
            self.assertEqual(check_results.main([BAD]), 1)


class Cluster(unittest.TestCase):
    def test_normalise_strips_numbers_ids_paths(self):
        a = cluster.normalise("Fillet failed: edge 4412 not found in /tmp/a1b2/part.sc @ fillet")
        b = cluster.normalise("Fillet failed: edge 77 not found in /home/u/x.sc @ fillet")
        self.assertEqual(a, b)
        self.assertNotIn("4412", a)

    def test_groups_by_cause(self):
        groups = cluster.cluster(cluster.load_failures([GOOD], ITEMS))
        self.assertEqual(sorted(len(v) for v in groups.values()), [1, 2])

    def test_no_paths_or_raw_numbers_in_title(self):
        import re
        groups = cluster.cluster(cluster.load_failures([GOOD], ITEMS))
        for key, recs in groups.items():
            text = cluster.draft(key, recs, ITEMS)
            self.assertIsNone(re.search(r"(?<![\w<])/[\w.]", text), text)
            title = text.splitlines()[0]
            self.assertNotIn("x.sc", text)
            self.assertNotIn("77", title)
            self.assertNotIn("4412", title)
        key = next(k for k, v in groups.items() if len(v) == 2)
        title = cluster.draft(key, groups[key], ITEMS).splitlines()[0]
        self.assertIn("edge N not found in <file>", title)

    def test_draft_and_known(self):
        groups = cluster.cluster(cluster.load_failures([GOOD], ITEMS))
        key = next(k for k, v in groups.items() if len(v) == 2)
        text = cluster.draft(key, groups[key], ITEMS)
        for needle in ("Title:", "Smallest failing item", "Items it covers", "2 item(s)",
                       "operation: fillet (constant)", "Counts by version",
                       "solidcraft 0.123.0: 1", "solidcraft 0.124.0: 1"):
            self.assertIn(needle, text)
        with tempfile.TemporaryDirectory() as d:
            kp = os.path.join(d, "known.json")
            with open(kp, "w") as w:
                json.dump({key: "https://example.invalid/1"}, w)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                cluster.main(["--known", kp, GOOD])
        self.assertNotIn("fillet failed", out.getvalue().lower())
        self.assertIn("volume 3% low", out.getvalue())


if __name__ == "__main__":
    unittest.main()
