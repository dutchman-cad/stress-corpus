"""Pipeline isolation and retention of FreeCAD outcomes in the public data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import site
from tools.common import load_items


class Site(unittest.TestCase):
    def test_all_freecad_outcomes_and_latest_causes_survive_build(self):
        items = list(load_items().values())[:8]
        records = [dict(id=item['id'], pipeline='freecad-dutchman', outcome=outcome,
                        signature='failure @ convert', history=dict(mapped=2, total=3))
                   for item, outcome in zip(items, site.OUTCOMES)]
        rerun = dict(records[2], outcome='correct', rerun_of='old')
        native = dict(records[3], pipeline='solidcraft')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'results').mkdir()
            (root / 'items.jsonl').write_text(''.join(json.dumps(i)+'\n' for i in items))
            (root / 'results/batch-01.jsonl').write_text(
                ''.join(json.dumps(r)+'\n' for r in records+[native, rerun]))
            with patch.object(site, 'ROOT', tmp), patch.object(site, 'snapshot_results', return_value=None):
                data = site.build()
        route = data['results']['freecad-dutchman']
        self.assertEqual(len(route), 8)
        self.assertEqual(route['7'][0], site.OUTCOMES.index('source-failed'))
        self.assertEqual(route['2'][0], site.OUTCOMES.index('correct'))
        self.assertEqual(data['history']['freecad-dutchman'], dict(mapped=16, total=24))
        causes = [g for g in data['causes'] if g['pipeline']=='freecad-dutchman']
        self.assertEqual(sum(len(g['items']) for g in causes), 3)
        ids = {iid for g in causes for iid in g['items']}
        self.assertNotIn(items[2]['id'], ids)
        self.assertNotIn(items[7]['id'], ids)
        self.assertEqual(len(data['results']['solidcraft']), 1)
        self.assertEqual(data['fixed']['per_pipeline'], {'freecad-dutchman': 1})

    def test_causes_do_not_merge_pipelines(self):
        rec = dict(id='S0001', outcome='wrong-refusal', signature='no @ extrude')
        groups = site.current_causes([dict(rec, pipeline=p) for p in
                                     ('solidcraft', 'freecad-dutchman')], {})
        self.assertEqual(len(groups), 2)
        self.assertEqual({g['pipeline'] for g in groups}, {'solidcraft', 'freecad-dutchman'})
