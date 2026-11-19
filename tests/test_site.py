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

    def test_render_preserves_anchors_and_no_script_results(self):
        from contextlib import redirect_stdout
        from html.parser import HTMLParser
        from io import StringIO

        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []
                self.panels = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs:
                    self.ids.append(attrs['id'])
                if tag == 'article':
                    self.panels.append(attrs['id'])

        items = list(load_items().values())[:3]
        records = [dict(id=it['id'], pipeline='solidcraft', outcome=outcome,
                        signature='<bad> @ build') for it, outcome in
                   zip(items, ['correct', 'wrong-result', 'hang'])]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'results').mkdir()
            (root / 'items.jsonl').write_text(''.join(json.dumps(i)+'\n' for i in items))
            (root / 'results/batch-01.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
            with patch.object(site, 'ROOT', tmp), patch.object(site, 'snapshot_results', return_value=None):
                data = site.build()
                with patch.object(site, 'build', return_value=data), redirect_stdout(StringIO()):
                    self.assertEqual(site.main(), 0)
            page = (root / 'site/index.html').read_text()
            self.assertEqual(json.loads((root / 'site/data.json').read_text()), data)
        parser = Elements()
        parser.feed(page)
        self.assertEqual(len(parser.ids), len(set(parser.ids)), 'Duplicate IDs break hash navigation')
        self.assertEqual(parser.panels, ['overview', 'grid', 'progress', 'failures', 'issues', 'take-part'])
        for anchor in ['solidcraft-vs-freecad', 'route-title', 'totals', 'before-stress-test-vs-now',
                       'the-grid', 'where-the-fails-are', 'current-failures-by-cause',
                       'one-axis-at-a-time', 'fixed-since', 'issues-from-the-corpus', 'how-to-take-part']:
            self.assertIn(anchor, parser.ids)
        self.assertNotIn('@@', page)
        content = site.static_content(data)
        self.assertEqual(content['stats'].count('class="stat"'), 6)
        self.assertIn('<b>3</b><span>Items</span>', content['stats'])
        self.assertIn('<b>1</b><span>Correct</span>', content['stats'])
        self.assertIn('<b>1</b><span>Wrong</span>', content['stats'])
        self.assertIn('<b>1</b><span>Hangs</span>', content['stats'])
        self.assertIn('&lt;bad&gt;', content['current-failures-by-cause'])
        self.assertIn(items[1]['id'], content['current-failures-by-cause'])
        self.assertIn('<svg', content['the-grid'])
        self.assertIn('<noscript>', content['solidcraft-vs-freecad'])
        self.assertNotIn('<h2>Totals</h2>', page)
        self.assertIn('wrong results + wrong refusals', content['stats'])
        data['progress'] = {'solidcraft': {'fixed': 7, 'reg': 2}}
        content = site.static_content(data)
        self.assertIn('<b>7</b><span>Fixed</span>', content['stats'])
        self.assertIn('Failed before stress test; now passing', content['stats'])
        self.assertIn('>2</span>', content['regressions'])

    def test_progress_counts_snapshot_failures_and_regressions_per_route(self):
        comparison = dict(then={
            'solidcraft': {'0': [2], '1': [3], '2': [4], '3': [0], '4': [6]},
            'freecad-dutchman': {'0': [2]}},
            builds={'solidcraft': {'0': 'new', '2': 'new', '3': 'new'}})
        results = {'solidcraft': {'0': [0], '1': [1], '2': [4], '3': [3], '4': [0]},
                   'freecad-dutchman': {'0': [2]}}
        progress = site.progress_totals(comparison, results)
        st = progress['solidcraft']
        self.assertEqual((st['fixed'], st['reg'], st['still']), (2, 1, 1))
        self.assertEqual((st['rerun'], st['thenFail'], st['notyet']), (3, 3, 1))
        self.assertEqual(st['fixedIds'], [0, 1])
        self.assertEqual(st['regIds'], [3])
        self.assertEqual(progress['freecad-dutchman']['fixed'], 0)
        self.assertEqual(site.progress_totals(None, results), {})
