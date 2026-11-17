#!/usr/bin/env python3
"""Build the public results page: site/index.html and site/data.json.

Reads items.jsonl and results/*.jsonl from the repository root. Standard library only.
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AXES = ["operation", "surface", "topology", "gap", "placement", "scale", "order"]
OUTCOMES = ["correct", "correct-refusal", "wrong-result", "wrong-refusal", "hang", "crash", "skipped"]
BATCH = 100
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
    for path in sorted(glob.glob(os.path.join(ROOT, "results", "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                n = index.get(r.get("id"))
                if n is None or r.get("outcome") not in OUTCOMES:
                    continue
                sig = short_brief(r.get("signature") or "", 160)
                results.setdefault(r.get("pipeline") or "solidcraft", {})[str(n)] = [
                    OUTCOMES.index(r["outcome"]), sig, r.get("link") or ""]
    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "batch": BATCH,
        "outcomes": OUTCOMES,
        "axes": AXES,
        "values": values,
        "items": rows,
        "results": results,
        "repo": REPO,
    }


def main():
    data = build()
    out = os.path.join(ROOT, "site")
    os.makedirs(out, exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out, "data.json"), "w", encoding="utf-8") as f:
        f.write(blob)
    safe = blob.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = PAGE.replace("/*DATA*/null", safe).replace("@@UPDATED@@", data["updated"]).replace("@@REPO@@", REPO)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    done = sum(1 for p in data["results"].values() for v in p.values() if v[0] != 6)
    print(f"wrote site/index.html ({len(page)} bytes), {done} results with an outcome")
    return 0


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stress corpus results</title>
<meta name="description" content="Pass and fail results for the 10,000 modelling tasks in the stress corpus.">
<style>
:root{--bg:#fafafa;--fg:#1d1f23;--mut:#5d6470;--card:#fff;--line:#d9dce1;--pass:#2e9e5b;--fail:#d6453d;--hang:#e5a50a;--none:#c9cdd3;--skip:#8a919c}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14161a;--fg:#e6e8ec;--mut:#9aa2ae;--card:#1c1f25;--line:#363b45;--pass:#3cb878;--fail:#ef5f56;--hang:#f0b429;--none:#3a3f49;--skip:#7d8591}}
:root[data-theme=dark]{--bg:#14161a;--fg:#e6e8ec;--mut:#9aa2ae;--card:#1c1f25;--line:#363b45;--pass:#3cb878;--fail:#ef5f56;--hang:#f0b429;--none:#3a3f49;--skip:#7d8591}
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
table{border-collapse:collapse;font-size:.8rem}
th,td{border:1px solid var(--line);padding:3px 5px;text-align:center;white-space:nowrap}
th{font-weight:600;background:var(--card)}th.l{text-align:left}
td.e{color:var(--mut)}
.hb{display:grid;grid-template-columns:minmax(110px,170px) 1fr auto;gap:4px 8px;align-items:center;font-size:.85rem}
.hb .t{background:var(--none);height:14px;border-radius:3px;overflow:hidden}.hb .t i{display:block;height:100%;background:var(--fail)}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
footer{margin:2em 0 1em;color:var(--mut);font-size:.9rem}
svg text{fill:var(--mut);font-size:10px}
</style>
</head>
<body>
<main>
<h1>Stress corpus results</h1>
<p class="mut">10,000 small modelling tasks, one per cell, in ID order. Each row is one batch of 100. Red is a bug found; grey is not tried yet.</p>

<div class="bar"><span class="seg" id="pipes" role="group" aria-label="Pipeline"></span>
<span class="legend" id="legend"></span></div>

<h2>The grid</h2>
<div class="card"><div id="gridwrap"><canvas id="grid" aria-label="Grid of all 10,000 tasks"></canvas></div>
<div id="detail" class="mut">Hover or tap a cell for details.</div></div>
<div id="tip"></div>

<h2>Totals</h2>
<div class="tot" id="totals"></div>
<p class="mut" id="totnote"></p>

<h2>Do the fails go down as we go?</h2>
<p class="mut">Fail rate for each batch, in batch order: (wrong result + wrong refusal + hang + crash) divided by tasks done. Batches with nothing done are left empty.</p>
<div class="card"><svg id="trend" viewBox="0 0 600 150" width="100%" role="img" aria-label="Fail rate per batch"></svg></div>

<h2>Where the fails are</h2>
<p class="mut">Fail rate by operation and topology: fails out of done. A dash means nothing has been run there yet. Only tasks with a result count.</p>
<div class="card scroll"><table id="heat"></table></div>

<h2>One axis at a time</h2>
<div class="cols" id="axes"></div>

<footer><p><a href="@@REPO@@">Repository</a> &middot; <a href="@@REPO@@#readme">README</a> &middot; Last updated @@UPDATED@@</p></footer>
</main>
<script>
"use strict";
var D=/*DATA*/null;
var OUT=D.outcomes, B=D.batch, N=D.items.length, AX=D.axes;
var FAILS={2:1,3:1,4:1,5:1}; // wrong-result, wrong-refusal, hang, crash
var PASS={0:1,1:1};
var HANG={4:1,5:1};
var NAMES=["Correct","Correct refusal","Wrong result","Wrong refusal","Hang","Crash","Skipped"];
var pipes=Object.keys(D.results); if(!pipes.length)pipes=["solidcraft"];
pipes.sort(function(a,b){return a==="solidcraft"?-1:b==="solidcraft"?1:a<b?-1:1});
var cur=pipes[0];
var $=function(id){return document.getElementById(id)};
function css(n){return getComputedStyle(document.documentElement).getPropertyValue(n).trim()}
function pct(a,b){return b?(100*a/b).toFixed(1)+"%":"-"}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function res(n){return (D.results[cur]||{})[n]}
function isDone(r){return r&&r[0]!==6}

function label(p){return p==="solidcraft"?"SolidCraft":p==="fusion-dutchman"?"Fusion via Dutchman":p}
function buildPipes(){
  var el=$("pipes"); el.innerHTML="";
  if(pipes.length<2){el.style.display="none";return}
  pipes.forEach(function(p){var b=document.createElement("button");b.textContent=label(p);
    b.setAttribute("aria-pressed",p===cur);b.onclick=function(){cur=p;buildPipes();all()};el.appendChild(b)});
}
function legend(){
  $("legend").innerHTML=[["pass","Pass"],["fail","Fail"],["hang","Hang or crash"],["skip","Skipped"],["none","Not started"]].map(function(x){
    return '<span><i class="sw '+x[0]+'" style="'+(x[0]==="skip"?"":"background:var(--"+x[0]+")")+'"></i>'+x[1]+"</span>"}).join("");
}
function colour(r){
  if(!r)return["fill","none"];
  var o=r[0]; if(o===6)return["skip","skip"];
  if(PASS[o])return["fill","pass"]; if(HANG[o])return["fill","hang"]; return["fill","fail"];
}
var geo={};
function drawGrid(){
  var cv=$("grid"),W=$("gridwrap").clientWidth,lab=W<500?22:30,cw=(W-lab)/B,ch=Math.max(cw,3),H=Math.ceil(ch*B);
  var dpr=window.devicePixelRatio||1;
  cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);cv.style.height=H+"px";
  var g=cv.getContext("2d");g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,W,H);
  var col={pass:css("--pass"),fail:css("--fail"),hang:css("--hang"),none:css("--none"),skip:css("--skip"),mut:css("--mut")};
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
  var cv=$("grid"),rc=cv.getBoundingClientRect(),x=ev.clientX-rc.left-geo.lab,y=ev.clientY-rc.top;
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
  var cv=$("grid"),tip=$("tip");
  cv.addEventListener("mousemove",function(e){var n=cellAt(e);if(n<0){tip.style.display="none";return}
    tip.innerHTML=describe(n).h;tip.style.display="block";
    var x=e.clientX+14,y=e.clientY+14;if(x+330>innerWidth)x=Math.max(4,e.clientX-334);if(y+120>innerHeight)y=e.clientY-110;
    tip.style.left=x+"px";tip.style.top=y+"px"});
  cv.addEventListener("mouseleave",function(){tip.style.display="none"});
  cv.addEventListener("click",function(e){var n=cellAt(e);if(n<0)return;var d=describe(n);
    $("detail").innerHTML=d.h+(d.link?'<br><a href="'+esc(d.link)+'">Issue</a>':"")});
}
function counts(){
  var c=[0,0,0,0,0,0,0];
  for(var k in (D.results[cur]||{}))c[D.results[cur][k][0]]++;
  return c;
}
function totals(){
  var c=counts(),done=0,fails=0;
  for(var i=0;i<6;i++){done+=c[i];if(FAILS[i])fails+=c[i]}
  var tried=done+c[6];
  var cards=[["Done",done+" of "+N.toLocaleString()],["Fail rate",pct(fails,done)+" ("+fails+" of "+done+")"]];
  for(var i=0;i<7;i++)cards.push([NAMES[i],String(c[i])]);
  cards.push(["Not started",String(N-tried)]);
  $("totals").innerHTML=cards.map(function(x){return"<div><span class=mut>"+x[0]+"</span><b>"+esc(x[1])+"</b></div>"}).join("");
  $("totnote").textContent="Done means built or refused, with an outcome other than skipped. Skipped tasks are left out of every rate.";
}
function trend(){
  var rows=Math.ceil(N/B),W=600,H=150,l=30,b=18,t=8,bw=(W-l-4)/rows,s=[];
  var data=[],mx=0.05;
  for(var r=0;r<rows;r++){var d=0,f=0;for(var n=r*B;n<Math.min(N,(r+1)*B);n++){var x=res(n);if(isDone(x)){d++;if(FAILS[x[0]])f++}}
    data.push([d,f]);if(d&&f/d>mx)mx=f/d}
  mx=Math.min(1,Math.ceil(mx*10)/10);
  var ph=H-b-t;
  [0,.5,1].forEach(function(q){var y=t+ph*(1-q);s.push('<line x1="'+l+'" x2="'+W+'" y1="'+y+'" y2="'+y+'" stroke="'+css("--line")+'"/><text x="'+(l-4)+'" y="'+(y+3)+'" text-anchor="end">'+Math.round(q*mx*100)+'%</text>')});
  data.forEach(function(d,r){var x=l+r*bw;
    if(d[0]){var h=ph*(d[1]/d[0])/mx;s.push('<rect x="'+x+'" y="'+(t+ph-h)+'" width="'+Math.max(bw-1,1)+'" height="'+Math.max(h,1)+'" fill="'+css("--fail")+'"><title>Batch '+(r+1)+': '+d[1]+' of '+d[0]+' failed ('+pct(d[1],d[0])+')</title></rect>')}
    else s.push('<rect x="'+x+'" y="'+(t+ph-1)+'" width="'+Math.max(bw-1,1)+'" height="1" fill="'+css("--none")+'"/>')});
  [1,25,50,75,100].forEach(function(r){if(r<=rows)s.push('<text x="'+(l+(r-.5)*bw)+'" y="'+(H-4)+'" text-anchor="middle">'+r+'</text>')});
  $("trend").innerHTML=s.join("");
}
function shade(rate){var a=Math.min(1,.12+rate*.88);return"color-mix(in srgb, "+css("--fail")+" "+Math.round(a*100)+"%, transparent)"}
function tally(){
  var T={};AX.forEach(function(a,i){T[a]=D.values[a].map(function(){return[0,0]})});
  var heat={};
  for(var k in (D.results[cur]||{})){var r=D.results[cur][k];if(r[0]===6)continue;var it=D.items[+k];
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
function all(){drawGrid();totals();trend();tables()}
legend();buildPipes();wire();all();
var rt;addEventListener("resize",function(){clearTimeout(rt);rt=setTimeout(drawGrid,100)});
if(matchMedia)matchMedia("(prefers-color-scheme:dark)").addEventListener("change",all);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
