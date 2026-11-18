"""Generator contracts, runner stage attribution, and optional real FreeCAD smoke tests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import freecad
from tools import check_results
from tools.common import load_items


class Generator(unittest.TestCase):
    def item(self, surface="plane", operation="extrude"):
        return dict(id="S0001", surface=surface, operation=operation, topology="clean",
                    scale="normal", order="none", gap="none", placement="rotated + far",
                    expected={"bodies": 1, "refusal_ok": False})

    def test_standalone_parametric_macros(self):
        for surface in ("plane", "cylinder", "cone", "sphere", "torus", "spline"):
            with self.subTest(surface=surface):
                macro = freecad.macro_for(self.item(surface))
                compile(macro, "macro.py", "exec")
                self.assertIn('"PartDesign::Body"', macro)
                self.assertIn('"Sketcher::SketchObject"', macro)
                self.assertIn('doc.saveAs', macro)
                self.assertNotIn('Part.makeBox', macro)

    def test_unsupported_names_step_and_preserves_expected(self):
        item = self.item(operation="thread")
        r = freecad.record(item)
        self.assertIsNone(r["macro"])
        self.assertIn("solid.thread", r["unsupported"])
        self.assertEqual(r["expected"], item["expected"])

    def test_tolerance_matches_results_validator_including_tiny_volume(self):
        expected = {"bodies": 1, "volume_mm3": 0.001}
        self.assertTrue(freecad.compare(expected, {"bodies": 1, "volume_mm3": .0010000005}))
        self.assertFalse(freecad.compare(expected, {"bodies": 1, "volume_mm3": .00100001}))
        self.assertFalse(freecad.compare(expected, {"bodies": 2, "volume_mm3": .001}))

    def test_history_counts(self):
        self.assertEqual(freecad.history_counts({"history": {"mapped": 3, "total": 7}}),
                         {"mapped": 3, "total": 7})
        self.assertIsNone(freecad.history_counts({"ok": False}))


class Runner(unittest.TestCase):
    def run_fake(self, source_code=0, convert_code=0, cli_code=0, timeout_stage=None,
                 source_bodies=1, measured_bodies=1, refusal=False):
        args = argparse.Namespace(freecad="fc", dutchman="dm", cli="sc", timeout=10)
        rec = dict(id="S0001", macro="pass", expected={"bodies": 1, "refusal_ok": refusal})
        versions = dict(freecad="FreeCAD 1", dutchman="Dutchman 2", solidcraft="SolidCraft 3")
        def process(argv, cwd, timeout):
            if argv[0] == timeout_stage:
                raise subprocess.TimeoutExpired(argv, timeout)
            if argv[0] == "fc":
                if source_code == 0:
                    (cwd / "S0001.built.json").write_text(json.dumps(dict(version="FreeCAD 1",bodies=source_bodies,volume_mm3=1)))
                    (cwd / "S0001.step").write_text("step")
                    (cwd / "S0001.FCStd").write_text("document")
                return source_code, "", "error: source error"
            if argv[0] == "dm":
                return convert_code, json.dumps({"history":{"mapped":3,"total":7},"ok":convert_code==0}), "error: conversion error" if convert_code else ""
            return cli_code, json.dumps({"body_count":measured_bodies,"total":{"volume_mm3":1}}), "error: replay error" if cli_code else ""
        with tempfile.TemporaryDirectory() as tmp, patch.object(freecad,"process",side_effect=process):
            return freecad.run_one(rec,args,versions,tmp)

    def test_correct_retains_versions_and_history(self):
        r = self.run_fake()
        self.assertEqual(r["outcome"],"correct")
        self.assertEqual(r["history"],{"mapped":3,"total":7})
        self.assertEqual(len(r["versions"]),3)
        self.assertIn("body count only",r["detail"])

    def test_source_failure_is_never_an_acceptable_refusal(self):
        for kwargs in ({"source_code":1,"refusal":True}, {"timeout_stage":"fc"}, {"source_bodies":2}):
            self.assertEqual(self.run_fake(**kwargs)["outcome"],"source-failed")

    def test_downstream_outcomes(self):
        for kwargs,want in (({"convert_code":1},"wrong-refusal"),
                            ({"cli_code":1,"refusal":True},"correct-refusal"),
                            ({"convert_code":-11},"crash"),
                            ({"timeout_stage":"dm"},"hang"),
                            ({"timeout_stage":"sc"},"hang"),
                            ({"measured_bodies":2},"wrong-result")):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.run_fake(**kwargs)["outcome"],want)

    def test_schema_and_source_signature(self):
        items = load_items()
        r = self.run_fake(source_code=1)
        self.assertEqual(check_results.check_line(r,items,"test")[0],[])
        del r["signature"]
        self.assertTrue(check_results.check_line(r,items,"test")[0])
        r.update(signature="error @ source",pipeline="solidcraft")
        self.assertTrue(check_results.check_line(r,items,"test")[0])


@unittest.skipUnless(os.environ.get("FREECAD_CMD"), "set FREECAD_CMD for headless API smoke tests")
class FreeCADIntegration(Generator):
    def test_real_feature_history(self):
        cases = [(s,"extrude") for s in ("plane","cylinder","cone","sphere","torus","spline")]
        cases += [("plane",op) for op in ("revolve","sweep","loft","shell","draft","mirror","pattern (linear)","pattern (circular)","boolean join","boolean cut","boolean intersect","fillet (constant)","chamfer")]
        for surface,op in cases:
            with self.subTest(surface=surface,operation=op), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/"macro.py"
                path.write_text(freecad.macro_for(self.item(surface,op)))
                code,out,err = freecad.process([os.environ["FREECAD_CMD"],str(path)],tmp,120)
                error = Path(tmp)/"S0001.error.txt"
                self.assertEqual(code,0,out+err)
                self.assertTrue((Path(tmp)/"S0001.built.json").exists(),error.read_text() if error.exists() else out+err)
                # Inspect the saved document's history, rather than trusting a STEP alone.
                import zipfile
                with zipfile.ZipFile(Path(tmp)/"S0001.FCStd") as z:
                    xml = z.read("Document.xml").decode()
                self.assertIn('type="Sketcher::SketchObject"',xml)
                self.assertIn('type="PartDesign::Body"',xml)
                self.assertNotIn('type="Part::Feature"',xml)
