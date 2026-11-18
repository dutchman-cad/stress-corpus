"""Generator contracts, runner stage attribution, and optional real FreeCAD smoke tests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
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
                         {"mapped": 3, "total": 7, "partial": False})
        self.assertIsNone(freecad.history_counts({"ok": False}))


class Runner(unittest.TestCase):
    def run_fake(self, source_code=0, convert_code=0, cli_code=0, timeout_stage=None,
                 source_bodies=1, measured_bodies=1, refusal=False, partial=False):
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
                if partial:
                    write_design(Path(argv[3]), partial_doc())
                return convert_code, json.dumps({"mode":"partial_history" if partial else "history", "history":{"mapped":3,"total":7},"ok":convert_code==0}), "error: conversion error" if convert_code else ""
            if partial:
                with zipfile.ZipFile(argv[2]) as z:
                    doc = json.loads(z.read("document.json"))
                self.assertEqual([f["id"] for f in doc["features"]],[9,10])
            return cli_code, json.dumps({"body_count":measured_bodies,"total":{"volume_mm3":1}}), "error: replay error" if cli_code else ""
        with tempfile.TemporaryDirectory() as tmp, patch.object(freecad,"process",side_effect=process):
            return freecad.run_one(rec,args,versions,tmp)

    def test_correct_retains_versions_and_history(self):
        r = self.run_fake()
        self.assertEqual(r["outcome"],"correct")
        self.assertEqual(r["history"],{"mapped":3,"total":7,"partial":False})
        self.assertEqual(len(r["versions"]),3)
        self.assertIn("body count only",r["detail"])


    def test_partial_history_measures_selected_imports_and_still_catches_errors(self):
        r = self.run_fake(partial=True)
        self.assertEqual(r["outcome"],"correct")
        self.assertTrue(r["history"]["partial"])
        self.assertEqual(r["history"]["final_imports"],[{"id":9,"name":"arbitrary final name"},{"id":10,"name":"second solid"}])
        r = self.run_fake(partial=True,measured_bodies=2)
        self.assertEqual(r["outcome"],"wrong-result")
        self.assertIn("source: 1 mm³, 1 bodies",r["detail"])
        self.assertIn("converted: 1 mm³, 2 bodies",r["detail"])

    def test_item_files_are_deleted_on_every_outcome(self):
        args = argparse.Namespace(freecad="fc", dutchman="dm", cli="sc", timeout=10)
        rec = dict(id="S0001",macro="pass",expected={"bodies":1})
        versions = dict(freecad="fc",dutchman="dm",solidcraft="sc")
        with tempfile.TemporaryDirectory() as tmp, patch.object(freecad,"process",return_value=(1,"","error")):
            self.assertEqual(freecad.run_one(rec,args,versions,tmp)["outcome"],"source-failed")
            self.assertEqual(list(Path(tmp).iterdir()),[])

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



def partial_doc():
    return {"format":"solidcraft/23", "components":[
        {"id":5,"parent":0,"history_hidden":True}, {"id":6,"parent":5},
        {"id":7,"parent":0}], "occurrences":[{"id":20,"component":5},{"id":21,"component":7}],
        "visibility":{"hidden_occurrences":[[20]]}, "features":[
            {"id":1,"component":5,"type":"revolve"},
            {"id":2,"component":6,"type":"import","step_blob":"blobs/prefix.step"},
            {"id":9,"type":"import","name":"arbitrary final name","step_blob":"blobs/final.step"},
            {"id":10,"component":7,"type":"import","name":"second solid","step_blob":"blobs/final.step"}]}


def write_design(path, doc):
    with zipfile.ZipFile(path,"w") as z:
        z.writestr("document.json",json.dumps(doc))
        z.writestr("blobs/final.step",b"unchanged final geometry")
        z.writestr("blobs/prefix.step",b"prefix")


class PartialDesign(unittest.TestCase):
    def test_markers_and_descendants_select_imports_without_using_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = Path(tmp)/"in.sc",Path(tmp)/"out.sc"
            write_design(source,partial_doc())
            freecad.final_shape_design(source,dest)
            with zipfile.ZipFile(dest) as z:
                doc = json.loads(z.read("document.json"))
                self.assertEqual(z.read("blobs/final.step"),b"unchanged final geometry")
            self.assertEqual([f["id"] for f in doc["features"]],[9,10])
            self.assertEqual(doc["components"],[{"id":7,"parent":0}])
            self.assertEqual(doc["occurrences"],[{"id":21,"component":7}])

    def test_missing_marker_or_final_import_never_falls_back_to_all_bodies(self):
        for mutate in (lambda d:d.update(components=[]), lambda d:d.update(features=d["features"][:2])):
            with tempfile.TemporaryDirectory() as tmp:
                source,dest = Path(tmp)/"in.sc",Path(tmp)/"out.sc"
                doc = partial_doc()
                mutate(doc)
                write_design(source,doc)
                with self.assertRaises(ValueError):
                    freecad.final_shape_design(source,dest)


class Resume(unittest.TestCase):
    def test_append_and_recover_torn_tail(self):
        line = {"id":"S0001","pipeline":"freecad-dutchman","outcome":"correct"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"results.jsonl"
            path.write_bytes(json.dumps(line).encode()+b'\n{"id":')
            with freecad.result_log(path) as (f, seen):
                self.assertEqual(seen,{"S0001"})
                f.write((json.dumps(dict(line,id="S0002"))+"\n").encode())
            with freecad.result_log(path) as (_,seen):
                self.assertEqual(seen,{"S0001","S0002"})

    def test_completed_ids_are_not_built_or_generated_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(work_dir=tmp,jobs=4,min_free_gb=0)
            path = Path(tmp)/"results.jsonl"
            line = {"id":"S0001","pipeline":"freecad-dutchman","outcome":"skipped"}
            path.write_text(json.dumps(line)+"\n")
            recs = [{"id":"S0001"},{"id":"S0002","macro":"pass"}]
            with patch.object(freecad,"run_one",return_value=dict(line,id="S0002")) as run_one, patch.object(freecad,"record") as generate:
                freecad.run_batch(args,recs,path,{})
                run_one.assert_called_once()
                generate.assert_not_called()
            self.assertEqual(len(path.read_text().splitlines()),2)
            with patch.object(freecad,"run_one") as run_one:
                freecad.run_batch(args,recs,path,{})
                run_one.assert_not_called()

    def test_disk_guard_stops_before_build_or_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(work_dir=tmp,jobs=4,min_free_gb=20)
            with patch.object(freecad.shutil,"disk_usage",return_value=argparse.Namespace(free=19*1024**3)), patch.object(freecad,"run_one") as run_one:
                with self.assertRaisesRegex(OSError,"preserve 20 GiB"):
                    freecad.run_batch(args,[{"id":"S0001"}],Path(tmp)/"r.jsonl",{})
                run_one.assert_not_called()


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
