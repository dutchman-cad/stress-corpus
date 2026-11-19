#!/usr/bin/env python3
"""Build the public results page: site/index.html and site/data.json.

Reads items.jsonl and results/*.jsonl from the repository root. Standard library only.
"""
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster import display, kind, load_known, normalise  # noqa: E402
from common import FAILURES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AXES = ["operation", "surface", "topology", "gap", "placement", "scale", "order"]
OUTCOMES = ["correct", "correct-refusal", "wrong-result", "wrong-refusal", "hang", "crash", "skipped", "source-failed"]
BATCH = 100
SNAPSHOT = "before-stress-test"
SNAPSHOT_LABEL = "Before stress test (2026-11-17, SolidCraft 0.43.0)"
REPO = "https://github.com/dutchman-cad/stress-corpus"


def short_brief(text, limit=120):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def build():
    items = []
    with open(os.path.join(ROOT, "items.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    items.sort(key=lambda i: i["id"])
    index = {it["id"]: n for n, it in enumerate(items)}
    values = {a: sorted({it[a] for it in items}) for a in AXES}
    pos = {a: {v: n for n, v in enumerate(values[a])} for a in AXES}
    rows = [[it["id"]] + [pos[a][it[a]] for a in AXES] + [short_brief(it.get("brief"))] for it in items]

    results = {}
    builds = {}    # pipeline -> item index -> build of the newest re-run
    earlier = {}   # (pipeline, id) -> every first-run record
    reruns = []
    latest = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "results", "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                n = index.get(r.get("id"))
                if n is None or r.get("outcome") not in OUTCOMES:
                    continue
                if r.get("rerun_of"):
                    reruns.append(r)
                else:
                    earlier.setdefault((r.get("pipeline") or "solidcraft", r["id"]), []).append(r)
                    put(results, index, r)
                    latest[(r.get("pipeline") or "solidcraft", r["id"])] = r
    for r in reruns:  # a re-check is newer than the result it re-checks
        put(results, index, r)
        latest[(r.get("pipeline") or "solidcraft", r["id"])] = r
        builds.setdefault(r.get("pipeline") or "solidcraft", {})[str(index[r["id"]])] = (
            r.get("build") or r.get("version") or "")
    issues = load_issues()
    fixed = fixed_since(reruns, earlier, issues)
    comparison = compare(index, results, builds, issues)
    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "batch": BATCH,
        "outcomes": OUTCOMES,
        "axes": AXES,
        "values": values,
        "items": rows,
        "results": results,
        "issues": issues,
        "fixed": fixed,
        "causes": current_causes(latest.values(), issues),
        "history": history_totals(latest.values()),
        "compare": comparison,
        "progress": progress_totals(comparison, results),
        "repo": REPO,
    }


def snapshot_results(index):
    """Results frozen in the snapshot tag, read with git show. None when the tag is not there."""
    def git(*args):
        return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True,
                              encoding="utf-8", check=True).stdout
    try:
        names = [n for n in git("ls-tree", "-r", "--name-only", SNAPSHOT, "results/").split("\n")
                 if n.endswith(".jsonl")]
        blobs = [git("show", "%s:%s" % (SNAPSHOT, n)) for n in sorted(names)]
    except (OSError, subprocess.CalledProcessError):
        return None
    then, recs = {}, {}
    for blob in blobs:
        for line in blob.splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            n = index.get(r.get("id"))
            if n is None or r.get("outcome") not in OUTCOMES:
                continue
            pipe = r.get("pipeline") or "solidcraft"
            put(then, index, r)
            recs[(pipe, r["id"])] = r
    return then, recs


def newest_build(builds):
    seen = {b for per in builds.values() for b in per.values() if b}
    def key(b):
        return [int(t) if t.isdigit() else 0 for t in re.findall(r"\d+", b)] + [b]
    return max(seen, key=key) if seen else ""


def compare(index, results, builds, issues):
    """The snapshot against now: the per-cause table, and the data for the two grids."""
    snap = snapshot_results(index)
    if snap is None:
        return None
    then, recs = snap
    known_urls = {k: str(v).split() for k, v in load_known(os.path.join(ROOT, "known.json")).items()}
    titles = {i["url"]: "%s#%d %s" % (i["repo"].split("/")[1], i["number"], i["title"])
              for i in issues.get("issues", [])}
    ids = sorted(index, key=index.get)
    causes = {}
    for (pipe, iid), rec in recs.items():
        if rec["outcome"] not in FAILURES:
            continue
        key, url, text = cause_of(rec, known_urls)
        c = causes.setdefault((pipe, key), {"pipeline": pipe, "issue": url,
                                            "cause": titles.get(url) or text or url,
                                            "then": 0, "now": 0, "builds": set()})
        c["then"] += 1
        n = str(index[iid])
        now = results.get(pipe, {}).get(n)
        if now and now[0] in (0, 1):
            c["now"] += 1
            if builds.get(pipe, {}).get(n):
                c["builds"].add(builds[pipe][n])
    out = sorted(causes.values(), key=lambda c: (-c["then"], c["cause"]))
    for c in out:
        c["builds"] = sorted(c["builds"])
    return {"label": SNAPSHOT_LABEL, "then": then, "builds": builds,
            "newest": newest_build(builds), "causes": out}


def progress_totals(comparison, results):
    """Snapshot failures now passing, and regressions, shared by every progress display."""
    out = {}
    if not comparison:
        return out
    for pipe, then in comparison['then'].items():
        now = results.get(pipe, {})
        builds = comparison['builds'].get(pipe, {})
        st = dict(fixed=0, reg=0, still=0, rerun=0, notyet=0, thenFail=0,
                  fixedIds=[], regIds=[], changes={})
        for n, old in then.items():
            a, b = old[0], now.get(n, [6])[0]
            rerun = bool(builds.get(n))
            st['rerun'] += rerun
            if a in (2, 3, 4, 5):
                st['thenFail'] += 1
                st['notyet'] += not rerun
                if b in (0, 1):
                    st['fixed'] += 1
                    st['fixedIds'].append(int(n))
                    st['changes'][n] = 'fix'
                else:
                    st['still'] += 1
            elif a in (0, 1) and b in (2, 3, 4, 5):
                st['reg'] += 1
                st['regIds'].append(int(n))
                st['changes'][n] = 'reg'
            elif a != b and rerun:
                st['changes'][n] = 'chg'
        out[pipe] = st
    return out


def put(results, index, r):
    sig = short_brief(r.get("signature") or "", 160)
    results.setdefault(r.get("pipeline") or "solidcraft", {})[str(index[r["id"]])] = [
        OUTCOMES.index(r["outcome"]), sig, r.get("link") or ""]


def load_issues():
    try:
        with open(os.path.join(ROOT, "site", "issues.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"updated": "", "issues": []}


def cause_of(rec, known_urls):
    """Name the cause behind a failing result: its issue when it has one, else its signature."""
    key = " | ".join((rec.get("pipeline", ""), kind(rec["outcome"]), normalise(rec.get("signature") or "")))
    urls = known_urls.get(key, [])
    if rec.get("link"):
        urls = [rec["link"]]
    if urls:
        return urls[0], urls[0], ""
    return key, "", display(rec.get("signature") or "") or rec["outcome"]


def current_causes(records, issues):
    """Current failures by pipeline and cause, including routes absent from the snapshot."""
    known = {k: str(v).split() for k, v in load_known(os.path.join(ROOT, "known.json")).items()}
    titles = {i["url"]: i["title"] for i in issues.get("issues", [])}
    groups = {}
    for r in records:
        if r["outcome"] not in FAILURES:
            continue
        key, url, text = cause_of(r, known)
        pipe = r.get("pipeline") or "solidcraft"
        g = groups.setdefault((pipe, key), {"pipeline": pipe, "issue": url,
                                            "cause": titles.get(url) or text or url, "items": []})
        g["items"].append(r["id"])
    for g in groups.values():
        g["items"].sort()
    return sorted(groups.values(), key=lambda g: (-len(g["items"]), g["cause"]))


def history_totals(records):
    totals = {}
    for r in records:
        h = r.get("history")
        if not h:
            continue
        t = totals.setdefault(r["pipeline"], {"mapped": 0, "total": 0})
        for k in t:
            t[k] += h[k]
    return totals


def fixed_since(reruns, earlier, issues):
    """Items that failed on an earlier build and pass on the build a re-run used."""
    known_urls = {k: str(v).split() for k, v in load_known(os.path.join(ROOT, "known.json")).items()}
    titles = {i["url"]: "%s#%d %s" % (i["repo"].split("/")[1], i["number"], i["title"])
              for i in issues.get("issues", [])}
    groups, checked, still = {}, 0, 0
    per_pipeline = {}
    for r in reruns:
        pipe = r.get("pipeline") or "solidcraft"
        olds = [o for o in earlier.get((pipe, r["id"]), []) if o.get("outcome") in FAILURES]
        want = str(r["rerun_of"]).strip().lower()
        match = [o for o in olds if want in (str(o.get("version", "")).lower(), str(o.get("build", "")).lower())]
        old = (match or olds or [None])[-1]
        if old is None:
            continue
        checked += 1
        per_pipeline[pipe] = per_pipeline.get(pipe, 0) + 1
        if r["outcome"] not in ("correct", "correct-refusal"):
            still += 1
            continue
        key, url, text = cause_of(old, known_urls)
        to = r.get("build") or r.get("version") or ""
        g = groups.setdefault((pipe, key, str(r["rerun_of"]), to), {
            "pipeline": pipe, "issue": url, "cause": titles.get(url) or text or url,
            "from": str(r["rerun_of"]), "to": to, "items": []})
        g["items"].append(r["id"])
    out = sorted(groups.values(), key=lambda g: (-len(g["items"]), g["cause"]))
    for g in out:
        g["items"].sort()
    return {"checked": checked, "still": still, "groups": out, "per_pipeline": per_pipeline}


def static_content(data):
    """Readable, complete result sections for browsers without JavaScript."""
    from html import escape
    e = lambda value: escape(str(value), quote=True)
    names = ["Correct", "Correct refusal", "Wrong result", "Wrong refusal", "Hang", "Crash", "Skipped", "Source failed"]

    def table(heads, rows):
        if len(rows) > 10:
            return table(heads, rows[:10]) + '<details><summary class="table-toggle">Show all %d rows</summary>%s</details>' % (len(rows), table_full(heads, rows[10:]))
        return table_full(heads, rows)

    def table_full(heads, rows):
        return '<div class="card scroll"><table><thead><tr>' + ''.join(
            '<th scope="col">' + e(h) + '</th>' for h in heads
        ) + '</tr></thead><tbody>' + ''.join(
            '<tr>' + ''.join('<td style="white-space:normal">' + str(c) + '</td>' for c in row) + '</tr>'
            for row in rows
        ) + '</tbody></table></div>'

    def item_list(items):
        return '<details class="item-list"><summary>%d item IDs</summary><p>%s</p></details>' % (len(items), e(' '.join(items)))

    def cause(g):
        return '<a href="' + e(g['issue']) + '">' + e(g['cause']) + '</a>' if g['issue'] else e(g['cause'])

    def counts(pipe, source=None):
        out = [0] * len(OUTCOMES)
        for r in (source if source is not None else data['results']).get(pipe, {}).values():
            out[r[0]] += 1
        return out

    def grid(results):
        colors = ['pass', 'pass', 'fail', 'fail', 'hang', 'hang', 'skip', 'source']
        paths = {}
        for n in range(len(data['items'])):
            r = results.get(str(n))
            col = colors[r[0]] if r else 'none'
            paths.setdefault(col, []).append('M%d %dh1v1h-1z' % (n % BATCH, n // BATCH))
        cells = ''.join('<path fill="var(--%s)" d="%s"/>' % (col, ''.join(parts)) for col, parts in paths.items())
        return '<svg class="fallback-grid" role="img" aria-label="All tasks in ID order; outcome counts in the route comparison table" viewBox="0 0 %d %d">%s</svg>' % (
            BATCH, max(1, (len(data['items']) + BATCH - 1) // BATCH), cells)

    c = counts('solidcraft')
    fixed = data['progress'].get('solidcraft', {}).get('fixed', 0)
    stats = [('Items', len(data['items']), 'In the corpus'), ('Correct', c[0] + c[1], 'Including correct refusals'),
             ('Wrong', c[2] + c[3], 'wrong results + wrong refusals'), ('Hangs', c[4], 'Time limit exceeded'),
             ('Issues filed', len(data['issues']['issues']), 'Across the corpus'), ('Fixed', fixed, 'Failed before stress test; now passing')]
    content = {'stats': ''.join('<div class="stat"><b>' + format(n, ',') + '</b><span>' + label + '</span><small>' + note + '</small></div>' for label, n, note in stats)}
    regressions = data['progress'].get('solidcraft', {}).get('reg', 0)
    content['regressions'] = '<span id="progress-badge" class="regression-badge" aria-label="%d regressions"%s>%d</span>' % (regressions, '' if regressions else ' hidden', regressions)
    fallback = {key: '' for key in ('solidcraft-vs-freecad', 'the-grid', 'where-the-fails-are',
                                  'current-failures-by-cause', 'one-axis-at-a-time', 'fixed-since',
                                  'before-stress-test-vs-now', 'issues-from-the-corpus')}
    fallback['progress-banner'] = '<div class="regression-banner%s">%d regressions: passed before the stress test, fails now.</div>' % (' bad' if regressions else '', regressions) if data['compare'] else ''
    if not data['compare']:
        fallback['before-stress-test-vs-now'] = '<p>No before-stress-test results to compare with.</p>'
    routes = [('solidcraft', 'SolidCraft'), ('freecad-dutchman', 'FreeCAD via Dutchman')]
    cs = [counts(pipe) for pipe, _ in routes]
    rows = [('Items built (downstream outcome)', *[sum(c[:6]) for c in cs])]
    rows += [(name, *[c[n] for c in cs]) for n, name in enumerate(names)]
    rows += [('History features kept / total', *[
        str(data['history'][p]['mapped']) + ' / ' + str(data['history'][p]['total']) if p in data['history'] else 'Not recorded'
        for p, _ in routes])]
    fallback['solidcraft-vs-freecad'] = table(['Outcome', 'SolidCraft', 'FreeCAD via Dutchman'], rows)
    for pipe, label in routes:
        if pipe not in data['results']:
            continue
        heading = '<h3>' + label + '</h3>'
        results = data['results'][pipe]
        rate = lambda a, b: ('%.1f%%' % (100 * a / b)) if b else '-'
        fallback['the-grid'] += heading + '<p>Each row is one batch of 100. Red is a bug found; grey is not tried yet. Green: pass; yellow: hang or crash; purple: source failed.</p>' + grid(results)
        groups = [g for g in data['causes'] if g['pipeline'] == pipe]
        fallback['current-failures-by-cause'] += heading + table(['Cause', 'Failed items', 'Items'], [
            (cause(g), len(g['items']), item_list(g['items'])) for g in groups])
        groups = [g for g in data['fixed']['groups'] if g['pipeline'] == pipe]
        checked = data['fixed']['per_pipeline'].get(pipe, 0)
        fallback['fixed-since'] += heading + '<p>%d of %d re-checked items that failed before now pass.</p>' % (sum(len(g['items']) for g in groups), checked) + table(
            ['Cause', 'Before', 'Now', 'Fixed', 'Items'], [(cause(g), e(g['from']), e(g['to']), len(g['items']), item_list(g['items'])) for g in groups])
        tallies = {a: [[0, 0] for _ in data['values'][a]] for a in AXES}
        heat = {}
        for n, r in results.items():
            if r[0] in (6, 7):
                continue
            item = data['items'][int(n)]
            fail = int(r[0] in (2, 3, 4, 5))
            for i, axis in enumerate(AXES):
                t = tallies[axis][item[i + 1]]
                t[0] += 1
                t[1] += fail
            t = heat.setdefault((item[1], item[3]), [0, 0])
            t[0] += 1
            t[1] += fail
        fallback['where-the-fails-are'] += heading + table(['Operation'] + data['values']['topology'], [
            [e(op)] + ['%d/%d' % (heat[(i, j)][1], heat[(i, j)][0]) if (i, j) in heat else '–'
                       for j in range(len(data['values']['topology']))] for i, op in enumerate(data['values']['operation'])])
        fallback['one-axis-at-a-time'] += heading + table(['Axis', 'Value', 'Fail rate', 'Done'], [
            (axis, e(value), rate(tallies[axis][i][1], tallies[axis][i][0]), tallies[axis][i][0])
            for axis in ['surface', 'gap', 'placement', 'scale', 'order'] for i, value in enumerate(data['values'][axis])])
        comp = data['compare']
        if comp and pipe in comp['then']:
            then = comp['then'][pipe]
            builds = comp['builds'].get(pipe, {})
            st = data['progress'][pipe]
            fixed = st['fixed']
            regressions = [str(n) for n in st['regIds']]
            fallback['before-stress-test-vs-now'] += heading + '<p>%d items re-run. %d failing items now pass. %d regressions.</p>' % (st['rerun'], fixed, st['reg'])
            fallback['before-stress-test-vs-now'] += '<h4>' + e(comp['label']) + '</h4>' + grid(then) + '<h4>Now</h4>' + grid(results)
            fallback['before-stress-test-vs-now'] += table(['Cause', 'Failing then', 'Now passing', 'Fixed by build'], [
                (cause(g), g['then'], g['now'], e(', '.join(g['builds']))) for g in comp['causes'] if g['pipeline'] == pipe])
            if regressions:
                fallback['before-stress-test-vs-now'] += table(['Regression', 'Then', 'Now', 'Build'], [
                    (data['items'][int(n)][0], names[then[n][0]], names[results[n][0]], e(builds.get(n, ''))) for n in regressions])
    fallback['issues-from-the-corpus'] = '<p>Issues and linked fix pull requests. Checked ' + e(data['issues']['updated']) + '.</p>' + table(
        ['Issue', 'Title', 'Items', 'Fix pull requests', 'State'], [
            ('<a href="' + e(i['url']) + '">' + e(i['repo']) + ' #' + str(i['number']) + '</a>', e(i['title']), len(i['items']),
             ', '.join('<a href="' + e(pr['url']) + '">#' + str(pr['number']) + '</a> ' + e(pr['state']) for pr in i['prs']) or 'none', e(i['state']))
            for i in sorted(data['issues']['issues'], key=lambda i: (-len(i['items']), i['number']))])
    content.update({key: '<noscript>' + value + '</noscript>' for key, value in fallback.items()})
    return content


def main():
    data = build()
    out = os.path.join(ROOT, "site")
    os.makedirs(out, exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out, "data.json"), "w", encoding="utf-8") as f:
        f.write(blob)
    safe = blob.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = PAGE.replace("/*DATA*/null", safe).replace("@@UPDATED@@", data["updated"]).replace("@@REPO@@", REPO)
    content = static_content(data)
    page = page.replace("@@STATS@@", content.pop("stats")).replace("@@REGRESSIONS@@", content.pop("regressions"))
    page = re.sub(r"@@FALLBACK:([\w-]+)@@", lambda m: content.get(m[1], ""), page)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    done = sum(1 for p in data["results"].values() for v in p.values() if v[0] not in (6, 7))
    print(f"wrote site/index.html ({len(page)} bytes), {done} results with an outcome")
    return 0


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<noscript><style>#cmp,#sankey,#swipe,#gridwrap,#detail,#pipes,#tip,.card:has(>table:empty),.card:has(>#sankey),.card:has(>#gridwrap){display:none}</style></noscript>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stress corpus results</title>
<meta name="description" content="Pass and fail results for the 10,000 modelling tasks in the stress corpus.">
<style>
:root{--bg:#fafafa;--fg:#1d1f23;--mut:#5d6470;--card:#fff;--line:#d9dce1;--pass:#2e9e5b;--fail:#d6453d;--hang:#e5a50a;--none:#c9cdd3;--source:#9474b5;--skip:#8a919c}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14161a;--fg:#e6e8ec;--mut:#9aa2ae;--card:#1c1f25;--line:#363b45;--pass:#3cb878;--fail:#ef5f56;--hang:#f0b429;--none:#3a3f49;--source:#b293d2;--skip:#7d8591}}
:root[data-theme=dark]{--bg:#14161a;--fg:#e6e8ec;--mut:#9aa2ae;--card:#1c1f25;--line:#363b45;--pass:#3cb878;--fail:#ef5f56;--hang:#f0b429;--none:#3a3f49;--source:#b293d2;--skip:#7d8591}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:1.6rem;margin:.4em 0 .2em}h2{font-size:1.2rem;margin:1.6em 0 .4em}
p{margin:.4em 0}.mut{color:var(--mut)}a{color:inherit}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.bar{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:8px 0}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{background:var(--card);color:var(--fg);border:0;padding:6px 12px;font:inherit;cursor:pointer}
.seg button[aria-pressed=true]{background:var(--fg);color:var(--bg)}
.legend span{display:inline-flex;align-items:center;gap:6px;margin-right:12px;font-size:.9rem;white-space:nowrap}
.sw{width:12px;height:12px;border-radius:2px;display:inline-block}
.sw.skip{border:2px solid var(--skip);background:transparent}
.tot{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.tot div{border:1px solid var(--line);border-radius:6px;padding:8px;background:var(--card)}
.tot b{display:block;font-size:1.4rem}
#gridwrap{position:relative;width:100%}
canvas{display:block;width:100%;touch-action:manipulation}
#tip{position:fixed;z-index:5;pointer-events:none;max-width:320px;background:var(--fg);color:var(--bg);padding:8px 10px;border-radius:6px;font-size:.85rem;display:none}
#detail{min-height:4.5em;margin-top:8px;font-size:.92rem}
.scroll{overflow-x:auto}
#sankey .sl{font-size:12px;fill:var(--fg);paint-order:stroke;stroke:var(--card);stroke-width:3px}
#sankey .sn{fill:var(--mut);font-weight:600}
#sankey .sh{font-size:11px;fill:var(--mut);text-transform:uppercase;letter-spacing:.04em}
#sankey .sd{font-size:12px;fill:var(--mut)}
table{border-collapse:collapse;font-size:.8rem}
th,td{border:1px solid var(--line);padding:3px 5px;text-align:center;white-space:nowrap}
th{font-weight:600;background:var(--card)}th.l{text-align:left}
td.e{color:var(--mut)}
.hb{display:grid;grid-template-columns:minmax(110px,170px) 1fr auto;gap:4px 8px;align-items:center;font-size:.85rem}
.hb .t{background:var(--none);height:14px;border-radius:3px;overflow:hidden}.hb .t i{display:block;height:100%;background:var(--fail)}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.cmpcols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:700px){.cmpcols{grid-template-columns:1fr}}
.cmpcols h3{font-size:1rem;margin:0 0 6px}
.cmpcols canvas{cursor:default}
.regression-banner,#regban{border-radius:8px;padding:12px 14px;font-weight:600;font-size:1.05rem;margin:8px 0;border:2px solid var(--pass);background:var(--card)}
.regression-banner.bad,#regban.bad{border-color:var(--fail);background:var(--fail);color:#fff}
#regban small{display:block;font-weight:400;font-size:.9rem}
.ol{display:inline-block;width:12px;height:12px;border:2px solid var(--fg);vertical-align:-2px;margin-right:4px}
.ol.r{border-color:var(--fail)}

.route-table{width:100%;table-layout:fixed;font-size:.95rem}
.route-table th,.route-table td{white-space:normal;padding:10px 12px;vertical-align:middle}
.route-table th:first-child{width:48%;text-align:left}
.route-table thead th{font-size:1.05rem}
.route-table tbody td{font-weight:650;font-variant-numeric:tabular-nums}
.route-table small{display:block;color:var(--mut);font-size:.8rem;font-weight:400;margin-top:2px}
.route-table .built td{font-size:1.25rem}
.route-table caption{text-align:left;color:var(--mut);caption-side:bottom;padding-top:10px;font-size:.85rem}
#solidcraft-vs-freecad{scroll-margin-top:16px}
#solidcraft-vs-freecad .card{padding:12px}
@media(max-width:480px){.route-table th,.route-table td{padding:8px 5px}.route-table thead th{font-size:.9rem}.route-table{font-size:.85rem}.route-table .built td{font-size:1.1rem}}

.take ol{margin:.4em 0 .8em 1.2em;padding:0}
.take li{margin:.25em 0}
footer{margin:2em 0 1em;color:var(--mut);font-size:.9rem}
svg text{fill:var(--mut);font-size:10px}

/* Navigation is an ordinary set of anchors until enhanced by JavaScript. */
:root{--accent:#176b57;--tint:#eaf3ef}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--accent:#7bd6b5;--tint:#20382f}}
:root[data-theme=dark]{--accent:#7bd6b5;--tint:#20382f}
main{max-width:1320px;padding:0 40px;margin:auto}
.masthead{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:36px 0 26px}
.eyebrow{font-size:.7rem;font-weight:750;letter-spacing:.14em;color:var(--accent);margin:0 0 10px}
h1{font-size:clamp(1.8rem,4vw,2.7rem);line-height:1.15;letter-spacing:-.04em;margin:0 0 10px}
.updated{font-size:.75rem;color:var(--mut);margin-top:10px;font-variant-numeric:tabular-nums}
.button,.table-toggle{display:inline-block;border:1px solid var(--line);border-radius:7px;padding:9px 14px;background:var(--card);color:var(--fg);font:inherit;cursor:pointer;text-decoration:none}
.primary{background:var(--accent);color:var(--card);border-color:var(--accent);font-weight:650;white-space:nowrap}
.tabbar{position:sticky;top:0;z-index:4;display:flex;gap:5px;overflow-x:auto;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 0;scrollbar-width:thin}
.tabbar a{flex:none;padding:9px 18px;border-radius:6px;text-decoration:none;color:var(--mut);font-size:.9rem;font-weight:650}
.regression-badge{display:inline-flex;align-items:center;justify-content:center;min-width:1.25em;padding:0 5px;margin-left:4px;border-radius:99px;background:var(--fail);color:#fff;font-size:.7rem;line-height:1.6}.regression-badge[hidden]{display:none}
.tabbar a:hover{background:var(--tint);color:var(--accent)}
.tabbar [aria-selected=true]{background:var(--fg);color:var(--bg)}
:focus-visible{outline:3px solid var(--accent);outline-offset:4px}
.route-context{font-size:.8rem;margin:14px 0 0}.workspace{display:grid;grid-template-columns:minmax(0,1fr);gap:40px;align-items:start}
.panels,.panel,.card,.cols>*{min-width:0}
.panel{padding:8px 0 32px;scroll-margin-top:82px}
.panel[hidden]{display:none}
.subsection,#solidcraft-vs-freecad{scroll-margin-top:90px;margin:24px 0 36px}
h2{font-size:1.25rem;letter-spacing:-.02em;margin:0 0 12px}
h2 a{text-decoration:none}h2 a:hover{text-decoration:underline}
.card{border-radius:10px;padding:16px}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px}
.stat{padding:18px;border:1px solid var(--line);border-radius:10px;background:var(--card)}
.stat b{display:block;font-size:2.2rem;line-height:1.25;letter-spacing:-.04em;font-variant-numeric:tabular-nums}
.stat span{display:block;font-size:.8rem;color:var(--mut);margin-top:6px}
.stat small{display:block;font-size:.7rem;color:var(--mut)}
.stat:first-child{background:var(--tint);border-color:var(--accent)}
.tot{grid-template-columns:repeat(auto-fit,minmax(135px,1fr))}
.tot b{font-size:1.15rem}.tot span{font-size:.8rem}
#outline{display:none}
@media(min-width:1100px){.enhanced .workspace{grid-template-columns:minmax(0,1fr) 180px}.enhanced #outline{display:block;position:sticky;top:96px;padding-left:18px;border-left:1px solid var(--line);margin-top:32px;font-size:.8rem}#outline p{font-size:.65rem;letter-spacing:.12em;font-weight:750;color:var(--mut);text-transform:uppercase;margin:0 0 12px}#outline a{display:block;text-decoration:none;color:var(--mut);padding:7px 0}#outline a:hover,#outline a[aria-current]{color:var(--accent)}}
.item-list summary{cursor:pointer;color:var(--accent);white-space:nowrap}.item-list p{max-width:28em;font-size:.75rem;line-height:1.7}
.table-toggle{font-size:.8rem;margin-top:12px}
tr[hidden]{display:none}
.scroll{max-width:100%;overscroll-behavior-x:contain}
table{width:100%}
p,li,td{overflow-wrap:anywhere}
#tip{max-width:min(320px,calc(100vw - 16px))}
.fallback-grid{width:100%;height:auto;display:block}
@media(max-width:600px){main{padding:0 16px}.masthead{align-items:flex-start;gap:12px;padding:24px 0 20px}.masthead .primary{padding:7px 10px;font-size:.8rem}.masthead .mut{font-size:.85rem}.eyebrow{font-size:.6rem}.tabbar{margin:0 -16px;padding:8px 16px;gap:2px}.tabbar a{padding:9px 13px}.workspace{gap:0}.stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.stat{padding:14px}.stat b{font-size:1.8rem}.card{padding:12px}.cols{grid-template-columns:minmax(0,1fr)}.hb{grid-template-columns:minmax(85px,110px) minmax(20px,1fr) auto;font-size:.75rem;gap:4px}.subsection{margin-top:20px}}
</style>
</head>
<body>
<main>
<header class="masthead">
<div><p class="eyebrow">DUTCHMAN CAD / STRESS CORPUS</p>
<h1>Stress corpus results</h1>
<p class="mut">10,000 modelling tasks testing SolidCraft and FreeCAD, one result at a time.</p>
<p class="updated">Last updated <time>@@UPDATED@@</time></p></div>
<a class="button primary" href="#take-part">Take part <span aria-hidden="true">↗</span></a>
</header>
<nav class="tabbar" aria-label="Results sections">
<a id="tab-overview" href="#overview">Overview</a>
<a id="tab-grid" href="#grid">Grid</a>
<a id="tab-progress" href="#progress">Progress @@REGRESSIONS@@</a>
<a id="tab-failures" href="#failures">Failures</a>
<a id="tab-issues" href="#issues">Issues</a>
<a id="tab-take-part" href="#take-part">Take part</a>
</nav>
<p class="mut route-context" id="route-context"></p>
<div class="workspace"><div class="panels">
<article id="overview" class="panel" aria-labelledby="tab-overview" data-label="Overview">
<section id="headline-totals" class="subsection">
<span id="outcome-totals"></span><span id="totals"></span>
<h2>At a glance</h2><p class="mut" id="headline-route">SolidCraft · latest committed results</p><div class="stats" id="headline-stats">@@STATS@@</div>
<p class="mut" id="totnote">Done means a downstream outcome. Skipped tasks and FreeCAD source failures are left out of every failure rate.</p>
</section>
<section id="solidcraft-vs-freecad" aria-labelledby="route-title">
<h2 id="route-title"><a href="#solidcraft-vs-freecad">SolidCraft vs FreeCAD: all 10,000 items</a></h2>
<p class="mut">The same tasks, two routes: built directly in SolidCraft, or built in FreeCAD and converted with Dutchman.</p>
<div class="card"><table class="route-table" id="route-comparison" aria-labelledby="route-title"></table></div>
@@FALLBACK:solidcraft-vs-freecad@@</section>
</article>
<article id="grid" class="panel" aria-labelledby="tab-grid" data-label="Grid">
<section id="the-grid" class="subsection">
<h2>The grid</h2>
<p class="mut">One cell per task, in ID order. Each row is one batch of 100. Red is a bug found; grey is not tried yet.</p>
<div class="bar"><span class="seg" id="pipes" role="group" aria-label="Grid route"></span></div>
<p class="mut" id="gridroute" aria-live="polite"></p>
<div class="bar"><span class="legend" id="legend"></span></div>
<div class="card"><div id="gridwrap"><canvas id="result-grid" aria-label="Grid of all 10,000 tasks"></canvas></div>
<div id="detail" class="mut">Hover or tap a cell for details.</div></div>
<div id="tip"></div>

@@FALLBACK:the-grid@@
</section>
<section id="where-the-fails-are" class="subsection">
<h2>Where the fails are</h2>
<p class="mut">Fail rate by operation and topology: fails out of done. A dash means nothing has been run there yet. Only tasks with a result count.</p>
<div class="card scroll"><table id="heat"></table></div>

@@FALLBACK:where-the-fails-are@@
</section>
</article>
<article id="progress" class="panel" aria-labelledby="tab-progress" data-label="Progress">
<section id="before-stress-test-vs-now" class="subsection">
<div id="progress-banner">@@FALLBACK:progress-banner@@</div>
<h2>Before stress test vs now</h2>
<div id="cmp"><p class="mut">No before-stress-test results to compare with.</p></div>

@@FALLBACK:before-stress-test-vs-now@@
</section>
</article>
<article id="failures" class="panel" aria-labelledby="tab-failures" data-label="Failures">
<section id="current-failures-by-cause" class="subsection">
<h2>Current failures by cause</h2>
<div class="card scroll"><table id="causes"></table></div>

@@FALLBACK:current-failures-by-cause@@
</section>
<section id="one-axis-at-a-time" class="subsection">
<h2>One axis at a time</h2>
<div class="cols" id="axes"></div>

@@FALLBACK:one-axis-at-a-time@@
</section>
<section id="fixed-since" class="subsection">
<h2>Fixed since</h2>
<p class="mut" id="fxnote"></p>
<div class="card scroll"><table id="fixed"></table></div>

@@FALLBACK:fixed-since@@
</section>
</article>
<article id="issues" class="panel" aria-labelledby="tab-issues" data-label="Issues">
<section id="issues-from-the-corpus" class="subsection">
<h2>Issues from the corpus</h2>
<p class="mut" id="isnote"></p>
<div class="card scroll"><svg id="sankey" viewBox="0 0 760 330" style="min-width:680px;width:100%;display:block" role="img" aria-label="Items run, their outcomes, failed items covered by issues, issues with a fix pull request, and the state of those pull requests"></svg></div>
<p class="mut" id="swipe" style="font-size:13px;margin:4px 0 0">Swipe sideways on a narrow screen.</p>
<div class="card scroll" style="margin-top:8px"><table id="istable"></table></div>

@@FALLBACK:issues-from-the-corpus@@
</section>
</article>
<article id="take-part" class="panel" aria-labelledby="tab-take-part" data-label="Take part">
<section id="how-to-take-part" class="subsection">
<h2>Take part</h2>
<div class="card take">
<p>Anyone can help, with or without an AI assistant, and no repo access is needed. Each batch is 100 small modelling tasks; build them, record what happened, and every failure becomes a bug report with the exact recipe attached.</p>
<ol>
<li><a href="@@REPO@@/issues?q=is%3Aissue+is%3Aopen+label%3Abatch+no%3Aassignee">Pick an open batch</a> and comment <code>/claim</code> on it. A bot assigns you at once.</li>
<li>Build the items in SolidCraft, by hand or with the generated scripts (<a href="@@REPO@@#two-ways-to-build-an-item">two ways to build an item</a>).</li>
<li>Open a results PR. When its checks pass it merges by itself, and this page updates within the hour.</li>
</ol>
<p><a href="@@REPO@@">Repository</a> &middot; <a href="@@REPO@@#take-part">How to take part</a> &middot; <a href="@@REPO@@#record-results">Recording results</a></p>
<p class="mut">Questions? <a href="@@REPO@@/issues/new">Open an issue</a>, or talk to the people working on it: <a href="https://discord.gg/yTcseBEdeu">join the Dutchman CAD Discord</a> and type <code>/stress-test</code>, which lets you into the #stress-corpus channel.</p>
</div>

@@FALLBACK:how-to-take-part@@
</section>
</article>
</div><aside id="outline" aria-label="On this tab"></aside></div>
<footer><p><a href="@@REPO@@">Repository</a> &middot; <a href="@@REPO@@#readme">README</a> &middot; Last updated @@UPDATED@@</p></footer>
</main>
<script>
"use strict";
var D=/*DATA*/null;
var OUT=D.outcomes, B=D.batch, N=D.items.length, AX=D.axes;
var FAILS={2:1,3:1,4:1,5:1}; // wrong-result, wrong-refusal, hang, crash
var PASS={0:1,1:1};
var HANG={4:1,5:1};
var NAMES=["Correct","Correct refusal","Wrong result","Wrong refusal","Hang","Crash","Skipped","Source failed"];
var pipes=Object.keys(D.results); if(!pipes.length)pipes=["solidcraft"];
pipes.sort(function(a,b){return a==="solidcraft"?-1:b==="solidcraft"?1:a<b?-1:1});
var cur=pipes.indexOf("solidcraft")>=0?"solidcraft":pipes[0];
var $=function(id){return document.getElementById(id)};
function css(n){return getComputedStyle(document.documentElement).getPropertyValue(n).trim()}
function pct(a,b){return b?(100*a/b).toFixed(1)+"%":"-"}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function res(n){return (D.results[cur]||{})[n]}
function isDone(r){return r&&r[0]!==6&&r[0]!==7}

function label(p){return p==="solidcraft"?"SolidCraft":p==="fusion-dutchman"?"Fusion via Dutchman":p==="freecad-dutchman"?"FreeCAD via Dutchman":p}
function buildPipes(){
  var el=$("pipes"); el.innerHTML="";
  if(pipes.length<2){el.style.display="none";return}
  pipes.forEach(function(p){var b=document.createElement("button");b.textContent=p==="freecad-dutchman"?"FreeCAD":label(p);
    b.setAttribute("aria-pressed",p===cur);b.onclick=function(){cur=p;$("detail").textContent="Hover or tap a cell for details.";$("tip").style.display="none";buildPipes();all()};el.appendChild(b)});
}
function legend(){
  $("legend").innerHTML=[["pass","Pass"],["fail","Fail"],["hang","Hang or crash"],["skip","Skipped"],["source","Source failed"],["none","Not started"]].map(function(x){
    return '<span><i class="sw '+x[0]+'" style="'+(x[0]==="skip"?"":"background:var(--"+x[0]+")")+'"></i>'+x[1]+"</span>"}).join("");
}
function colour(r){
  if(!r)return["fill","none"];
  var o=r[0]; if(o===6)return["skip","skip"]; if(o===7)return["fill","source"];
  if(PASS[o])return["fill","pass"]; if(HANG[o])return["fill","hang"]; return["fill","fail"];
}
var geo={};
function drawGrid(){
  var cv=$("result-grid");if(!cv.closest(".panel").hidden){drawVisibleGrid()}
}
function drawVisibleGrid(){
  var cv=$("result-grid"),W=$("gridwrap").clientWidth,lab=W<500?22:30,cw=(W-lab)/B,ch=Math.max(cw,3),H=Math.ceil(ch*B);
  var dpr=window.devicePixelRatio||1;
  cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);cv.style.height=H+"px";
  var g=cv.getContext("2d");g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,W,H);
  var col={pass:css("--pass"),fail:css("--fail"),hang:css("--hang"),none:css("--none"),skip:css("--skip"),source:css("--source"),mut:css("--mut")};
  geo={lab:lab,cw:cw,ch:ch};
  var gap=cw>=5?1:0;
  for(var n=0;n<N;n++){
    var x=lab+(n%B)*cw,y=Math.floor(n/B)*ch,c=colour(res(n));
    if(c[1]==="skip"){g.strokeStyle=col.skip;g.lineWidth=cw>=5?1:0.6;g.strokeRect(x+gap/2+.5,y+gap/2+.5,Math.max(cw-gap-1,1),Math.max(ch-gap-1,1))}
    else{g.fillStyle=col[c[1]];g.fillRect(x,y,cw-gap,ch-gap)}
  }
  g.fillStyle=col.mut;g.font="10px system-ui,sans-serif";g.textBaseline="middle";g.textAlign="right";
  var every=ch>=9?1:ch>=5?5:10, rows=Math.ceil(N/B);
  for(var r=0;r<rows;r+=every){var t=r+1;g.fillText(t<10?"0"+t:""+t,lab-4,r*ch+ch/2)}
}
function cellAt(ev){
  var cv=$("result-grid"),rc=cv.getBoundingClientRect(),x=ev.clientX-rc.left-geo.lab,y=ev.clientY-rc.top;
  if(x<0)return -1;
  var c=Math.floor(x/geo.cw),r=Math.floor(y/geo.ch);
  if(c<0||c>=B||r<0)return -1;var n=r*B+c;return n<N?n:-1;
}
function describe(n){
  var it=D.items[n],r=res(n),o=r?NAMES[r[0]]:"Not started";
  var s=it[0]+" \u00b7 batch "+(Math.floor(n/B)+1)+" \u00b7 "+o;
  var h='<b>'+esc(s)+'</b><br>'+esc(it[8]);
  if(r&&r[1])h+='<br><span class="mut">Signature:</span> '+esc(r[1]);
  return{h:h,link:r&&r[2]};
}
function wire(){
  var cv=$("result-grid"),tip=$("tip");
  cv.addEventListener("mousemove",function(e){var n=cellAt(e);if(n<0){tip.style.display="none";return}
    tip.innerHTML=describe(n).h;tip.style.display="block";
    var x=e.clientX+14,y=e.clientY+14;if(x+330>innerWidth)x=Math.max(4,e.clientX-334);if(y+120>innerHeight)y=e.clientY-110;
    tip.style.left=x+"px";tip.style.top=y+"px"});
  cv.addEventListener("mouseleave",function(){tip.style.display="none"});
  cv.addEventListener("click",function(e){var n=cellAt(e);if(n<0)return;var d=describe(n);
    $("detail").innerHTML=d.h+(d.link?'<br><a href="'+esc(d.link)+'">Issue</a>':"")});
}
function counts(pipe){
  var c=OUT.map(function(){return 0}),R=D.results[pipe||cur]||{};
  for(var k in R)c[R[k][0]]++;
  return c;
}
function routeComparison(){
  var routes=["solidcraft","freecad-dutchman"],cs=routes.map(counts);
  var rows=[["Items built","Tasks with a downstream outcome; excludes skipped tasks and source failures.",function(c){return c.slice(0,6).reduce(function(a,b){return a+b},0)}]];
  [[0,"The result matches the expected measurements."],
   [1,"An invalid task was refused as expected."],
   [2,"It built, but the result differs from the expected measurements."],
   [3,"A valid task was refused."],
   [4,"The run did not finish within the time limit."],
   [5,"The program stopped unexpectedly."],
   [7,"The source program itself could not build the task."],
   [6,"The task is not expressible by this route yet."]].forEach(function(x){
    if(x[0]===5&&!cs.some(function(c){return c[5]}))return;
    rows.push([NAMES[x[0]],x[1],function(c){return c[x[0]]}]);
  });
  var h='<caption>Latest committed result per item and route. Each route covers '+N.toLocaleString()+' tasks. Missing history measurements mean “Not recorded”.</caption>'
    +'<thead><tr><th scope="col">Outcome</th><th scope="col">SolidCraft<small>Direct build</small></th><th scope="col">FreeCAD<small>via Dutchman</small></th></tr></thead><tbody>';
  rows.forEach(function(row,i){h+='<tr'+(i===0?' class="built"':'')+'><th scope="row">'+esc(row[0])+'<small>'+esc(row[1])+'</small></th>'
    +cs.map(function(c){return '<td>'+row[2](c).toLocaleString()+'</td>'}).join("")+'</tr>'});
  h+='<tr><th scope="row">History features kept / total<small>Features mapped into editable history out of all reported source features.</small></th>'
    +routes.map(function(p){var t=(D.history||{})[p];return '<td>'+(t?t.mapped.toLocaleString()+' / '+t.total.toLocaleString()+'<small>'+pct(t.mapped,t.total)+'</small>':'<span class="mut">Not recorded</span>')+'</td>'}).join("")+'</tr></tbody>';
  $("route-comparison").innerHTML=h;
}
function headline(){
  var c=counts(),st=D.progress[cur]||{fixed:0,reg:0};
  var values=[N,c[0]+c[1],c[2]+c[3],c[4],D.issues.issues.length,st.fixed];
  document.querySelectorAll('#headline-stats .stat b').forEach(function(el,i){el.textContent=values[i].toLocaleString()});
  $('headline-route').textContent=label(cur)+' · latest committed results';
  var badge=$('progress-badge');badge.textContent=st.reg;badge.hidden=!st.reg;
  badge.setAttribute('aria-label',st.reg+(st.reg===1?' regression':' regressions'));
}
function pipelineIssues(){
  var urls={};
  (D.causes||[]).forEach(function(g){if(g.pipeline===cur&&g.issue)urls[g.issue]=1});
  (D.fixed.groups||[]).forEach(function(g){if(g.pipeline===cur&&g.issue)urls[g.issue]=1});
  if(D.compare)(D.compare.causes||[]).forEach(function(g){if(g.pipeline===cur&&g.issue)urls[g.issue]=1});
  return ((D.issues&&D.issues.issues)||[]).filter(function(i){return urls[i.url]});
}
function flows(){
  var L=pipelineIssues(),R=D.results[cur]||{},f={run:0,pass:0,fail:0,skip:0,cov:0,unf:0,ni:0,pr:0,nopr:0,merged:0,open:0,closed:0};
  var by={},hit={};
  L.forEach(function(i){
    var st="";
    if(i.prs.length){var m=i.prs.some(function(p){return p.state==="merged"}),o=i.prs.some(function(p){return p.state==="open"});st=m?"merged":o?"open":"closed"}
    i.prstate=st;
    (i.items||[]).forEach(function(id){(by[id]=by[id]||[]).push(i)});
  });
  for(var k in R){var r=R[k][0],id=D.items[+k][0];f.run++;
    if(r===6||r===7)f.skip++;else if(PASS[r])f.pass++;
    else{f.fail++;if(by[id]){f.cov++;by[id].forEach(function(i){hit[i.url]=i})}else f.unf++}}
  Object.keys(hit).forEach(function(u){var i=hit[u];f.ni++;if(i.prstate){f.pr++;f[i.prstate]++}else f.nopr++});
  return f;
}
function sankey(){
  var f=flows(),svg=$("sankey");
  if(!f.run){svg.innerHTML="";return}
  var W=760,H=330,nw=14,top=26,bot=8,lab=150,pad=10;
  var cx=[0,0,0,0,0,0].map(function(_,i){return i*((W-nw-lab)/5)});
  var avail=H-top-bot, ink=css("--fg"), out=[], pos={};
  // items scale: tallest item column is stage 1 (everything); leave room for gaps and thin bands
  var s1=(avail-pad*2-6)/f.run;
  function band(k,x,y,v,s,name,col,txt){var h=Math.max(v*s,v?3:0);pos[k]={x:x,y:y,h:h,out:0,inn:0};
    out.push('<rect x="'+x+'" y="'+y+'" width="'+nw+'" height="'+h+'" fill="'+col+'"><title>'+esc(name+": "+txt)+"</title></rect>");
    out.push('<text x="'+(x+nw+6)+'" y="'+(y+Math.min(h,30)/2+4)+'" class="sl">'+esc(name)+' <tspan class="sn">'+esc(txt)+"</tspan></text>");return h}
  function link(a,b,v,col,s,name){if(!v)return;var A=pos[a],B=pos[b],h=Math.max(v*s,1);
    var y0=A.y+A.out,y1=B.y+B.inn;A.out+=h;B.inn+=h;var x0=A.x+nw,x1=B.x,mx=(x0+x1)/2;
    out.push('<path d="M'+x0+" "+y0+"C"+mx+" "+y0+" "+mx+" "+y1+" "+x1+" "+y1+"L"+x1+" "+(y1+h)+"C"+mx+" "+(y1+h)+" "+mx+" "+(y0+h)+" "+x0+" "+(y0+h)+'Z" fill="'+col+'" fill-opacity=".3"><title>'+esc(name+": "+v)+"</title></path>")}
  var n=function(v){return v.toLocaleString()};
  var heads=["Items run","Outcome","Failed items","Issues","Fix PR?","PR state"];
  heads.forEach(function(t,i){out.push('<text x="'+cx[i]+'" y="12" class="sh">'+t+"</text>")});
  // stage 1
  band("run",cx[0],top,f.run,s1,"Items run",ink,n(f.run));
  // stage 2: pass, fail, skip (in this order, top to bottom)
  var y=top;
  y+=band("pass",cx[1],y,f.pass,s1,"Passed",css("--pass"),n(f.pass))+pad;
  var yf=y;y+=band("fail",cx[1],y,f.fail,s1,"Failed",css("--fail"),n(f.fail))+pad;
  band("skip",cx[1],y,f.skip,s1,"Skipped / source failed",css("--skip"),n(f.skip));
  link("run","pass",f.pass,css("--pass"),s1,"Run to passed");link("run","fail",f.fail,css("--fail"),s1,"Run to failed");link("run","skip",f.skip,css("--skip"),s1,"Run to skipped");
  // stage 3: covered / not yet filed, aligned with the failed band
  y=yf;
  y+=band("cov",cx[2],y,f.cov,s1,"Covered by an issue",css("--hang"),n(f.cov))+pad/2;
  band("unf",cx[2],y,f.unf,s1,"Not yet filed",css("--none"),n(f.unf));
  link("fail","cov",f.cov,css("--hang"),s1,"Failed to covered");link("fail","unf",f.unf,css("--none"),s1,"Failed to not yet filed");
  // stage 4: the covered items land in issues; label is the issue count
  y=pos.cov.y;
  var h4=band("iss",cx[3],y,f.cov,s1,f.ni+(f.ni===1?" issue":" issues"),css("--hang"),n(f.cov)+" items");
  link("cov","iss",f.cov,css("--hang"),s1,"Covered items to issues");
  // unit change divider
  var dx=cx[3]+nw+ (cx[4]-cx[3]-nw)/2;
  out.push('<line x1="'+dx+'" x2="'+dx+'" y1="'+(top-4)+'" y2="'+H+'" stroke="'+css("--mut")+'" stroke-dasharray="4 4"/>');
  out.push('<text x="'+dx+'" y="'+(H-12)+'" text-anchor="middle" class="sd">items → issues</text>');
  // stages 5 and 6 count issues, own scale: the whole column is the issue count
  var s2=Math.max(0.1,(avail-pad*3-40)/Math.max(f.ni,1));
  var i0=top;
  i0+=band("pr",cx[4],i0,f.pr,s2,"Has a fix PR",css("--pass"),n(f.pr)+" issues")+pad;
  band("nopr",cx[4],i0,f.nopr,s2,"No PR yet",css("--none"),n(f.nopr)+" issues");
  y=top;
  y+=band("merged",cx[5],y,f.merged,s2,"Merged",css("--pass"),n(f.merged)+" issues")+pad;
  y+=band("open",cx[5],y,f.open,s2,"Open",css("--hang"),n(f.open)+" issues")+pad;
  band("closed",cx[5],y,f.closed,s2,"Closed unmerged",css("--fail"),n(f.closed)+" issues");
  link("pr","merged",f.merged,css("--pass"),s2,"To merged");link("pr","open",f.open,css("--hang"),s2,"To open");link("pr","closed",f.closed,css("--fail"),s2,"To closed unmerged");
  svg.setAttribute("viewBox","0 0 "+W+" "+H);
  svg.innerHTML=out.join("");
  $("isnote").textContent=n(f.run)+" items run: "+n(f.pass)+" passed, "+n(f.fail)+" failed, "+n(f.skip)+" skipped or source failed. "+n(f.cov)+" of the failed items are covered by "+f.ni+" issues and "+n(f.unf)+" are not yet filed. Left of the dashed line the bands count items; right of it they count issues. "+f.pr+" issues have a fix pull request: "+f.merged+" merged, "+f.open+" open, "+f.closed+" closed without merging. A fix pull request is one that says it fixes the issue or is cross-referenced from it. "+(D.issues.updated?"Checked "+D.issues.updated+".":"");
}
function issueTable(){
  // Biggest first: issues covering the most items at the top (contributor's ask, 2026-11-17).
  var L=pipelineIssues().slice().sort(function(a,b){return b.items.length-a.items.length||a.number-b.number});
  if(!L.length){$("istable").innerHTML="<tr><td>No issues yet.</td></tr>";return}
  var h='<tr><th class="l">Issue</th><th class="l">Title</th><th>Items</th><th class="l">PR</th><th>State</th></tr>';
  L.forEach(function(i){
    var prs=i.prs.length?i.prs.map(function(p){return'<a href="'+esc(p.url)+'">#'+p.number+"</a> "+p.state}).join(", "):"none";
    h+='<tr><td class="l" style="text-align:left"><a href="'+esc(i.url)+'">'+esc(i.repo.split("/")[1])+" #"+i.number+'</a></td><td style="text-align:left;white-space:normal;min-width:220px">'+esc(i.title)+"</td><td>"+i.items.length+'</td><td style="text-align:left">'+prs+"</td><td>"+esc(i.state)+"</td></tr>"});
  $("istable").innerHTML=h;
}
function fixedSince(){
  var base=D.fixed||{checked:0,groups:[]};
  var F={checked:(base.per_pipeline||{})[cur]||0,groups:base.groups.filter(function(g){return g.pipeline===cur})};
  if(!F.checked){$("fxnote").textContent="No re-runs yet. When a failed item is checked again on a newer build and passes, it shows up here, grouped by cause.";$("fixed").innerHTML="";return}
  var n=0;F.groups.forEach(function(g){n+=g.items.length});
  $("fxnote").textContent=n+" of "+F.checked+" re-checked items that failed before now pass.";
  var h='<tr><th class="l">Cause</th><th>Before</th><th>Now</th><th>Fixed</th><th class="l">Items</th></tr>';
  F.groups.forEach(function(g){
    var c=g.issue?'<a href="'+esc(g.issue)+'">'+esc(g.cause)+"</a>":esc(g.cause);
    h+='<tr><td style="text-align:left;white-space:normal;min-width:220px">'+c+"</td><td>"+esc(g.from)+"</td><td>"+esc(g.to)+"</td><td>"+g.items.length+'</td><td style="text-align:left;white-space:normal">'+itemList(g.items)+"</td></tr>"});
  $("fixed").innerHTML=h;
}
function shade(rate){var a=Math.min(1,.12+rate*.88);return"color-mix(in srgb, "+css("--fail")+" "+Math.round(a*100)+"%, transparent)"}
function tally(){
  var T={};AX.forEach(function(a,i){T[a]=D.values[a].map(function(){return[0,0]})});
  var heat={};
  for(var k in (D.results[cur]||{})){var r=D.results[cur][k];if(!isDone(r))continue;var it=D.items[+k];
    var f=FAILS[r[0]]?1:0;
    AX.forEach(function(a,i){var c=T[a][it[i+1]];c[0]++;c[1]+=f});
    var key=it[1]+"|"+it[3],h=heat[key]||(heat[key]=[0,0]);h[0]++;h[1]+=f}
  return{T:T,heat:heat};
}
function tables(){
  var y=tally(),ops=D.values.operation,tops=D.values.topology;
  var h='<tr><th class="l">operation</th>'+tops.map(function(t){return"<th>"+esc(t)+"</th>"}).join("")+"</tr>";
  ops.forEach(function(o,i){h+='<tr><th class="l">'+esc(o)+"</th>"+tops.map(function(t,j){
    var c=y.heat[i+"|"+j];if(!c)return'<td class="e">\u2013</td>';
    var rate=c[1]/c[0];return'<td style="background:'+shade(rate)+'" title="'+c[1]+' of '+c[0]+' failed ('+pct(c[1],c[0])+')">'+c[1]+"/"+c[0]+"</td>"}).join("")+"</tr>"});
  $("heat").innerHTML=h;
  var out="";
  ["surface","gap","placement","scale","order"].forEach(function(a){
    var ai=AX.indexOf(a);out+='<div class="card"><b>'+a+'</b><div class="hb">';
    D.values[a].forEach(function(v,i){var c=y.T[a][i];
      out+="<span>"+esc(v)+'</span><span class="t"><i style="width:'+(c[0]?100*c[1]/c[0]:0)+'%"></i></span><span>'+(c[0]?pct(c[1],c[0])+" (n="+c[0]+")":"n=0")+"</span>"});
    out+="</div></div>"});
  $("axes").innerHTML=out;
}
var CMP={};
function cmpDraw(cv,wrap,get,mark){
  var W=wrap.clientWidth,lab=W<300?18:24,cw=(W-lab)/B,ch=Math.max(cw,2),H=Math.ceil(ch*B),dpr=window.devicePixelRatio||1;
  cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);cv.style.height=H+"px";
  var g=cv.getContext("2d");g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,W,H);
  var col={pass:css("--pass"),fail:css("--fail"),hang:css("--hang"),none:css("--none"),skip:css("--skip"),source:css("--source"),mut:css("--mut"),fg:css("--fg"),card:css("--card")};
  var gap=cw>=5?1:0,g2={lab:lab,cw:cw,ch:ch};
  for(var n=0;n<N;n++){
    var x=lab+(n%B)*cw,y=Math.floor(n/B)*ch,c=colour(get(n));
    if(c[1]==="skip"){g.strokeStyle=col.skip;g.lineWidth=.6;g.strokeRect(x+.5,y+.5,Math.max(cw-gap-1,1),Math.max(ch-gap-1,1))}
    else{g.fillStyle=col[c[1]];g.fillRect(x,y,cw-gap,ch-gap)}
  }
  if(mark){for(var n=0;n<N;n++){var m=mark(n);if(!m)continue;
    var x=lab+(n%B)*cw,y=Math.floor(n/B)*ch,w=cw-gap,h=ch-gap;
    if(m==="reg"){g.fillStyle=col.card;g.fillRect(x-1,y-1,w+2,h+2);g.strokeStyle=col.fail;g.lineWidth=2;g.strokeRect(x-.5,y-.5,w+1,h+1);
      g.fillStyle=col.fail;g.fillRect(x+1.5,y+1.5,Math.max(w-3,1),Math.max(h-3,1))}
    else{g.strokeStyle=col.fg;g.lineWidth=1.5;g.strokeRect(x-.25,y-.25,w+.5,h+.5)}}}
  g.fillStyle=col.mut;g.font="9px system-ui,sans-serif";g.textBaseline="middle";g.textAlign="right";
  for(var r=0;r<B;r+=(ch>=9?1:ch>=5?5:10)){var t=r+1;g.fillText(t<10?"0"+t:""+t,lab-3,r*ch+ch/2)}
  return g2;
}
function cmpCell(cv,geo,ev){var rc=cv.getBoundingClientRect(),x=ev.clientX-rc.left-geo.lab,y=ev.clientY-rc.top;
  if(x<0)return -1;var c=Math.floor(x/geo.cw),r=Math.floor(y/geo.ch);if(c<0||c>=B||r<0)return -1;var n=r*B+c;return n<N?n:-1}
function compare(){
  if($("progress").hidden)return;
  var C=D.compare,box=$("cmp");
  $("progress-banner").innerHTML="";
  if(!C){box.innerHTML='<p class="mut">No before-stress-test results to compare with.</p>';return}
  if(!C.then||!C.then[cur]){box.innerHTML='<p class="mut">No before-stress-test results for this pipeline.</p>';return}
  var T=(C.then&&C.then[cur])||{},R=D.results[cur]||{},BL=(C.builds&&C.builds[cur])||{};
  var st=D.progress[cur],chg=st.changes;
  var newest=C.newest||"",any=st.rerun>0;
  var ban=st.reg?'<div id="regban" class="bad" role="alert">'+st.reg+' regression'+(st.reg>1?"s":"")+': passed before the stress test, fails now.<small>Outlined in red in the right grid and listed below.</small></div>'
    :'<div id="regban" role="status">No regressions. Nothing that passed before the stress test fails now.'+(any?"":'<small>Nothing has been re-run yet.</small>')+'</div>';
  $("progress-banner").innerHTML=ban;
  var h="";
  h+=any?'<p class="mut">'+st.rerun+' items have been re-run on a newer build'+(newest?", the newest being "+esc(newest):"")+'. Items that were not re-run keep their before-stress-test result on the right.</p>'
    :'<p><b>Nothing has been re-run yet, so the two grids are identical.</b> When failed items are re-run on a newer build, the right grid updates and the changed cells get an outline.</p>';
  h+='<p class="legend mut"><span><i class="ol"></i>Changed since before the stress test</span><span><i class="ol r"></i>Regression</span></p>';
  h+='<div class="cmpcols"><div class="card"><h3>'+esc(C.label)+'</h3><div id="cw1"><canvas id="c1" aria-label="Grid before the stress test"></canvas></div></div>'
    +'<div class="card"><h3>Now'+(newest?" ("+esc(newest)+")":"")+'</h3><div id="cw2"><canvas id="c2" aria-label="Grid now"></canvas></div></div></div>';
  h+='<div class="tot" style="margin-top:12px">'+[["Failing, now passing",st.fixed],["Passing, now failing",st.reg],["Still failing",st.still],["Failed items re-run",st.thenFail-st.notyet+" of "+st.thenFail],["Failed items not yet re-run",st.notyet]].map(function(x){return"<div><span class=mut>"+x[0]+"</span><b>"+x[1]+"</b></div>"}).join("")+"</div>";
  h+='<div id="reglist"></div>';
  var cs=(C.causes||[]).filter(function(c){return c.pipeline===cur});
  if(cs.length){h+='<h3 style="margin:1em 0 .3em;font-size:1rem">By cause</h3><div class="card scroll"><table><tr><th class="l">Issue or cause</th><th>Failing then</th><th>Now passing</th><th>Fixed by build</th></tr>';
    cs.forEach(function(c){var t=c.issue?'<a href="'+esc(c.issue)+'">'+esc(c.cause)+"</a>":esc(c.cause);
      h+='<tr><td style="text-align:left;white-space:normal;min-width:220px">'+t+"</td><td>"+c.then+"</td><td>"+c.now+"</td><td>"+(c.builds.length?esc(c.builds.join(", ")):"–")+"</td></tr>"});
    h+="</table></div>"}
  box.innerHTML=h;
  var rl="";
  if(st.reg){rl='<div class="card" style="border-color:var(--fail);margin-top:12px"><b>Regressions</b><ul style="margin:.4em 0 0;padding-left:1.2em">';
    st.regIds.forEach(function(n){var r=R[n];rl+="<li>"+esc(D.items[n][0])+" · "+esc(NAMES[T[n][0]])+" then, "+esc(NAMES[r[0]])+" now"+(BL[n]?" on "+esc(BL[n]):"")+(r[1]?" · "+esc(r[1]):"")+"</li>"});
    rl+="</ul></div>"}
  $("reglist").innerHTML=rl;
  var c1=$("c1"),c2=$("c2"),tip=$("tip");
  var g1=cmpDraw(c1,$("cw1"),function(n){return T[n]},null);
  var g2=cmpDraw(c2,$("cw2"),function(n){return R[n]},function(n){var m=chg[n];return m==="reg"?"reg":m?"chg":null});
  [[c1,g1],[c2,g2]].forEach(function(p){
    p[0].addEventListener("mousemove",function(e){var n=cmpCell(p[0],p[1],e);if(n<0){tip.style.display="none";return}
      var a=T[n],b=R[n];tip.innerHTML="<b>"+esc(D.items[n][0])+"</b><br>Then: "+esc(a?NAMES[a[0]]:"Not started")+"<br>Now: "+esc(b?NAMES[b[0]]:"Not started")+(BL[n]?" ("+esc(BL[n])+")":"");
      tip.style.display="block";var x=e.clientX+14,y=e.clientY+14;if(x+330>innerWidth)x=Math.max(4,e.clientX-334);if(y+100>innerHeight)y=e.clientY-90;tip.style.left=x+"px";tip.style.top=y+"px"});
    p[0].addEventListener("mouseleave",function(){tip.style.display="none"})});
}
function itemList(items){
  return '<details class="item-list"><summary>'+items.length+' item IDs</summary><p>'+esc(items.join(' '))+'</p></details>';
}
function causeTable(){
  var groups=(D.causes||[]).filter(function(g){return g.pipeline===cur});
  var h='<tr><th class="l">Cause</th><th>Failed items</th><th class="l">Items</th></tr>';
  groups.forEach(function(g){var c=g.issue?'<a href="'+esc(g.issue)+'">'+esc(g.cause)+'</a>':esc(g.cause);
    h+='<tr><td style="text-align:left;white-space:normal;min-width:220px">'+c+'</td><td>'+g.items.length+'</td><td style="text-align:left;white-space:normal">'+itemList(g.items)+'</td></tr>'});
  $("causes").innerHTML=groups.length?h:'<tr><td>No current failures for this pipeline.</td></tr>';
}

function collapseTables(){
  document.querySelectorAll('.panel table').forEach(function(table){
    var rows=Array.from(table.rows).filter(function(row){return row.querySelector('td')});
    var host=table.closest('.scroll')||table.parentElement;
    var button=host.nextElementSibling;
    if(button&&button.classList.contains('table-toggle'))button.remove();
    if(rows.length<=10)return;
    var expanded=table.dataset.expanded==='true';
    rows.forEach(function(row,i){row.hidden=i>=10&&!expanded});
    button=document.createElement('button');button.className='table-toggle';
    if(!table.id)table.id='table-'+Array.from(document.querySelectorAll('table')).indexOf(table);
    button.setAttribute('aria-controls',table.id);
    function update(){button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?'Show top 10':'Show all '+rows.length+' rows'}
    update();button.onclick=function(){expanded=!expanded;table.dataset.expanded=String(expanded);rows.forEach(function(row,i){row.hidden=i>=10&&!expanded});update()};
    host.after(button);
  });
}
function setupTabs(){
  var tabs=Array.from(document.querySelectorAll('.tabbar a'));
  var panels=Array.from(document.querySelectorAll('.panel'));
  document.documentElement.classList.add('enhanced');
  document.querySelector('.tabbar').setAttribute('role','tablist');
  document.querySelector('.tabbar').setAttribute('aria-label','Results sections');
  tabs.forEach(function(tab,i){
    tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panels[i].id);
    panels[i].setAttribute('role','tabpanel');
    tab.addEventListener('keydown',function(e){
      var next=e.key==='ArrowRight'?(i+1)%tabs.length:e.key==='ArrowLeft'?(i+tabs.length-1)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:-1;
      if(next<0)return;e.preventDefault();tabs[next].focus();
      if(location.hash===tabs[next].hash)activate(true);else location.hash=tabs[next].hash;
    });
    tab.addEventListener('click',function(){if(location.hash===tab.hash)activate(true)});
  });
  function activate(scroll){
    var id;try{id=decodeURIComponent(location.hash.slice(1))}catch(e){id='overview'}
    var target=$(id||'overview'),panel=target&&(target.classList.contains('panel')?target:target.closest('.panel'));
    if(!panel)panel=$('overview');
    panels.forEach(function(p,i){var active=p===panel;p.hidden=!active;tabs[i].setAttribute('aria-selected',String(active));tabs[i].tabIndex=active?0:-1});
    var selected=tabs[panels.indexOf(panel)];
    // Keep the selected tab visible without moving the page vertically.
    var bar=selected.parentElement;bar.scrollLeft=Math.max(0,selected.offsetLeft-bar.offsetLeft-(bar.clientWidth-selected.offsetWidth)/2);
    var links=Array.from(panel.querySelectorAll('.subsection, #solidcraft-vs-freecad'));
    $('outline').innerHTML='<p>On this tab</p>'+links.map(function(s){return '<a href="#'+s.id+'"'+(target===s?' aria-current="location"':'')+'>'+esc(s.querySelector('h2').textContent)+'</a>'}).join('');
    drawGrid();compare();collapseTables();
    target=$(id||panel.id);
    if(scroll&&target)target.scrollIntoView({block:'start'});
  }
  addEventListener('hashchange',function(){activate(true)});
  activate(!!location.hash);
}

function all(){$("route-context").innerHTML='Viewing '+esc(label(cur))+' results · <a href="#grid">Change route</a>';$("gridroute").textContent=label(cur)+" · the grid and breakdowns show this route.";$("result-grid").setAttribute("aria-label",label(cur)+": grid of all "+N.toLocaleString()+" tasks");causeTable();drawGrid();headline();tables();sankey();issueTable();compare();fixedSince();collapseTables()}
routeComparison();legend();buildPipes();wire();all();setupTabs();
var rt;addEventListener("resize",function(){clearTimeout(rt);rt=setTimeout(function(){drawGrid();compare();collapseTables()},100)});
if(matchMedia)matchMedia("(prefers-color-scheme:dark)").addEventListener("change",all);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
