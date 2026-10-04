"""Generate an offline review desk; all tag text is rendered as text, never HTML."""

import json

from ao3_benchmark import fingerprint


def slim_profile(profile):
    keys = ("n", "words_n", "words_sum", "bands", "complete", "restricted", "languages", "sample_n", "co_tags")
    return {k: profile[k] for k in keys}


def write_review_html(root, cases, catalogue, aliases, population, fields):
    packet_hash = fingerprint(cases)
    compact = [{**c, "profile": slim_profile(c["profile"]),
                "candidates": [{**t, "profile": slim_profile(t["profile"])} for t in c["candidates"]]} for c in cases]
    data = {"packet": packet_hash, "cases": compact, "fields": fields,
            "catalogue": [[r["canonical_id"], r["name"], r["cached_count"], [a["name"] for a in aliases.get(r["canonical_id"], [])]] for r in catalogue],
            "unmerged": [[r["source_id"], r["name"], r["cached_count"]] for r in population]}
    # Escaping '<' also makes the payload safe if it is later inlined in a script.
    encoded = json.dumps(data, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    (root / "review-data.js").write_text("window.REVIEW_DATA=" + encoded + ";\n")
    (root / "review.html").write_text(HTML)


HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AO3 unmerged tag review</title>
<style>
:root{font:16px/1.5 system-ui,sans-serif;color:#202c36;background:#f6f5f1}*{box-sizing:border-box}body{margin:0}header{padding:1rem 2rem;background:#213b48;color:white;position:sticky;top:0;z-index:2}h1{font-size:1.2rem;margin:0 0 .6rem}h2{font-size:1.35rem;overflow-wrap:anywhere}h3{font-size:1rem;margin:.5rem 0}button,input,select,textarea{font:inherit}button{cursor:pointer;background:#fff;border:1px solid #a4b4ba;border-radius:5px;padding:.35rem .7rem;color:#173947}button:hover{background:#e4eef0}button:disabled{opacity:.5;cursor:default}header button{margin:.15rem}main{max-width:1500px;margin:auto;padding:1.5rem;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(320px,1fr);gap:1.5rem}section,.box{background:#fff;border:1px solid #d9dedc;border-radius:8px;padding:1rem;margin-bottom:1rem}.box{background:#fbfcfc}label{display:block;margin:.7rem 0 .2rem;font-weight:600}input,textarea,select{max-width:100%;padding:.4rem;border:1px solid #9ba9ae;border-radius:4px}textarea,input[type=text]{width:100%}textarea{min-height:7rem}p{margin:.5rem 0}.muted,small{color:#566874}.candidate{border-top:1px solid #dde4e3;padding:.65rem 0;overflow-wrap:anywhere}.candidate button{float:right;margin-left:.5rem}.stats{font-size:.9rem}details{margin:.7rem 0}summary{cursor:pointer}#status{font-size:.9rem}#save-status{color:#e0ecce}aside{align-self:start}a{color:#1c5971}.results{max-height:420px;overflow:auto}.tag{display:inline-block;padding:.1rem .35rem;margin:.15rem;background:#edf2f2;border-radius:3px;font-size:.85rem}@media(max-width:850px){main{display:block;padding:.8rem}header{position:static;padding:1rem}}
</style>
<header><h1>AO3 unmerged tag review · 2021 snapshot</h1>
<button id="prev">← Previous</button><button id="next">Next →</button>
<select id="filter" aria-label="Review filter"><option value="all">All cases</option><option value="pending">Unreviewed</option><option value="calibration">Calibration</option><option value="validation">Validation</option><option value="challenge">Challenge</option></select>
<button id="export">Export judgments CSV</button><button id="backup">Export backup JSON</button><button id="import">Import backup JSON</button><input id="file" type="file" accept="application/json" hidden>
<div id="status"></div><div id="save-status" role="status"></div></header>
<main><div><section><h2 id="name"></h2><p id="source"></p><div id="profile"></div>
<details><summary>Review instructions</summary><p>Judge whether the whole meaning matches an existing Freeform canonical. Related, broader, or narrower concepts are not automatically equivalent. Retain qualifiers and fandom scope.</p><p>A new canonical is a useful, distinct concept after catalogue search. Low similarity alone does not establish novelty. Unclear, composite, expressive, wrong-type, or insufficiently supported tags can remain unresolved.</p><p>Counts describe works, not independent authors or popularity with readers. Context uses only the frozen development-work partition; missing context is not evidence of absence. This is a historical snapshot, not current AO3 policy.</p><p>Review suggested candidates, then search beyond them. Have another reviewer independently check new-canonical decisions and disagreements before making final claims. Import/export separate files for independent reviews.</p></details>
</section><section><h3>Candidate concepts</h3><p class="muted">Union of three top-ten lists, displayed by canonical ID. Scores are hidden initially to reduce anchoring.</p><div id="candidates"></div><details><summary>Show retrieval scores and sampling details</summary><pre id="scores" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details></section></div>
<aside><section><h3>Reference judgment</h3><p class="muted">These fields are your judgment, separate from retrieval or model proposals. Drafts save locally; export regularly.</p>
<label for="reviewer">Reviewer</label><input id="reviewer" type="text" autocomplete="off">
<label for="judgment">Decision</label><select id="judgment"><option value="">Unreviewed</option><option value="synonym">Synonym of an existing canonical</option><option value="new_canonical">Propose a new canonical</option><option value="unresolved">Leave unresolved</option></select>
<label for="canonical_id">Canonical ID (synonyms)</label><input id="canonical_id" type="text" inputmode="numeric"><p id="target-name" class="muted"></p>
<label for="proposed_name">Proposed name (optional, new canonicals)</label><input id="proposed_name" type="text" placeholder="Leave blank to decide the name later">
<label for="confidence">Confidence</label><select id="confidence"><option value="">Choose…</option><option>high</option><option>medium</option><option>low</option></select>
<label for="catalogue_checked">Searched the catalogue beyond this shortlist</label><select id="catalogue_checked"><option value="">Not recorded</option><option value="yes">Yes</option><option value="no">No</option></select>
<label for="rationale">Evidence and rationale</label><textarea id="rationale" placeholder="Explain equivalence or differences in scope; record search terms, supporting context, and uncertainty."></textarea><p id="validation" class="muted"></p>
</section><section><h3>Search the full Freeform catalogue</h3><p class="muted">All 181,067 canonical names and permitted alias examples. Enter words, or # followed by a tag ID. Search is literal; try alternate terms.</p><input id="search" type="text" placeholder="Search canonical names and aliases"><div id="search-results" class="results"></div></section>
<section><h3>Search other unmerged names</h3><p class="muted">The full visible Freeform pool. Shared words suggest cases to inspect; they do not establish equivalence or independent-user support.</p><input id="unmerged-search" type="text" placeholder="Search unmerged names"><div id="unmerged-results" class="results"></div></section></aside></main>
<script src="review-data.js"></script><script>
'use strict';
const D=window.REVIEW_DATA,$=id=>document.getElementById(id),key='ao3-review:'+D.packet;
const catalogue=new Map(D.catalogue.map(r=>[String(r[0]),r])),ids=new Set(D.cases.map(c=>String(c.source_id)));
let labels={},current=0,storageWorks=true,lastReviewer='';
try{labels=JSON.parse(localStorage.getItem(key)||'{}');lastReviewer=localStorage.getItem(key+':reviewer')||''}catch(e){storageWorks=false}
const fields=D.fields.filter(f=>f!=='source_id');
function text(parent,tag,value,cls){const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;parent.appendChild(e);return e}
function rowLabel(c){return labels[String(c.source_id)]||Object.fromEntries(D.fields.map(f=>[f,f==='source_id'?String(c.source_id):'']))}
function valid(r){if(!r.judgment)return 'Unreviewed';if(!r.reviewer.trim()||!r.rationale.trim()||!r.confidence)return 'Draft: reviewer, confidence and rationale are required.';
if(r.judgment==='synonym'&&(!catalogue.has(r.canonical_id)||r.proposed_name))return 'Draft: use one existing canonical ID and clear the proposed name.';
if(r.judgment==='new_canonical'&&(r.catalogue_checked!=='yes'||r.canonical_id))return 'Draft: record catalogue search and clear the canonical ID. A proposed name is optional.';
if(r.judgment==='unresolved'&&(r.canonical_id||r.proposed_name))return 'Draft: clear target fields; put possible candidates in the rationale.';
return 'Ready for review export';}
function persist(){try{localStorage.setItem(key,JSON.stringify(labels));localStorage.setItem(key+':reviewer',lastReviewer);$('save-status').textContent='Saved in this browser. Export a backup to keep a portable copy.'}catch(e){storageWorks=false;$('save-status').textContent='Browser storage unavailable: export before closing this page.'}}
function save(){const c=D.cases[current],r={source_id:String(c.source_id)};fields.forEach(f=>r[f]=$(f).value);if(r.reviewer.trim())lastReviewer=r.reviewer;labels[r.source_id]=r;persist();updateStatus();}
function updateStatus(){const r=rowLabel(D.cases[current]);$('validation').textContent=valid(r);$('target-name').textContent=catalogue.get(r.canonical_id)?.[1]||'';const ready=D.cases.filter(c=>valid(rowLabel(c))==='Ready for review export').length;$('status').textContent=`Case ${current+1} of ${D.cases.length} · ${ready} ready · ${D.cases.filter(c=>rowLabel(c).judgment).length} started`;}
function profile(parent,p){text(parent,'p',`${p.n.toLocaleString()} works in the evidence partition; co-tags from ${p.sample_n} sampled works.`, 'stats');if(p.words_n)text(parent,'p',`Mean length: ${Math.round(p.words_sum/p.words_n).toLocaleString()} words. Short / medium / long / epic: ${p.bands.join(' / ')}.`, 'stats');text(parent,'p','Work languages: '+(Object.entries(p.languages).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([k,v])=>`${k}: ${v}`).join(', ')||'unavailable'),'stats');for(const [type,tags]of Object.entries(p.co_tags)){if(!tags.length)continue;const line=text(parent,'p',type+': ','stats');tags.forEach(t=>text(line,'span',`${t.name} (${t.sample_works})`,'tag'));}}
function pick(id){$('judgment').value='synonym';$('canonical_id').value=String(id);$('proposed_name').value='';save();}
function render(){const c=D.cases[current],r=rowLabel(c);$('name').textContent=c.name;$('source').textContent=`Tag ${c.source_id} · approximate dump usage ${c.cached_count.toLocaleString()}`;$('profile').replaceChildren();profile($('profile'),c.profile);fields.forEach(f=>$(f).value=r[f]||(f==='reviewer'?lastReviewer:''));$('candidates').replaceChildren();for(const t of c.candidates){const e=text($('candidates'),'div','','candidate'),b=text(e,'button','Use this ID');b.addEventListener('click',()=>pick(t.canonical_id));text(e,'strong',t.name);text(e,'div',`ID ${t.canonical_id} · approximate usage ${t.cached_count.toLocaleString()}`,'muted');if(t.aliases.length)text(e,'p','Known examples: '+t.aliases.map(a=>a.name).join('; '),'stats');const details=document.createElement('details');text(details,'summary','Candidate work evidence');profile(details,t.profile);e.appendChild(details)}$('scores').textContent=JSON.stringify({arm:c.arm,partition:c.partition,selection_reason:c.selection_reason,rankings:c.rankings},null,2);updateStatus();}
function eligible(c){const f=$('filter').value;return f==='all'||(f==='pending'?!rowLabel(c).judgment:c.partition===f)}
function move(delta){save();for(let i=1;i<=D.cases.length;i++){const index=(current+delta*i+D.cases.length)%D.cases.length;if(eligible(D.cases[index])){current=index;render();window.scrollTo(0,0);return}}$('save-status').textContent='No cases match this filter.'}
$('prev').onclick=()=>move(-1);$('next').onclick=()=>move(1);$('filter').onchange=()=>{if(!eligible(D.cases[current]))move(1)};
fields.forEach(f=>$(f).addEventListener('input',save));
function download(name,content,type){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([content],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
const csvCell=s=>'"'+String(s??'').replaceAll('"','""')+'"';
$('export').onclick=()=>{save();const rows=[D.fields,...D.cases.map(c=>D.fields.map(f=>rowLabel(c)[f]))];download('judgments.csv',rows.map(r=>r.map(csvCell).join(',')).join('\n')+'\n','text/csv;charset=utf-8')};
$('backup').onclick=()=>{save();download('ao3-review-backup.json',JSON.stringify({packet:D.packet,labels},null,2),'application/json')};
$('import').onclick=()=>$('file').click();$('file').onchange=async e=>{try{const incoming=JSON.parse(await e.target.files[0].text());if(incoming.packet!==D.packet||!incoming.labels)throw new Error('Backup belongs to a different review packet.');for(const[id,r]of Object.entries(incoming.labels)){if(!ids.has(id)||String(r.source_id)!==id||D.fields.some(f=>typeof r[f]!=='string'))throw new Error('Invalid review row.');if(r.judgment&&!['synonym','new_canonical','unresolved'].includes(r.judgment))throw new Error('Unknown judgment.');}const conflicts=Object.keys(incoming.labels).filter(id=>labels[id]?.judgment&&incoming.labels[id].judgment&&JSON.stringify(labels[id])!==JSON.stringify(incoming.labels[id]));if(conflicts.length)throw new Error(`${conflicts.length} conflicting saved judgments. Use a separate browser profile for independent reviews; existing work was preserved.`);for(const[id,r]of Object.entries(incoming.labels)){if(!labels[id]?.judgment)labels[id]=r;}persist();render();$('save-status').textContent='Backup imported.'}catch(err){$('save-status').textContent=err.message}e.target.value=''};
function search(input,output,rows,canonical){let timer;$(input).addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{const q=$(input).value.trim().toLocaleLowerCase(),out=$(output);out.replaceChildren();if(!q)return;const tokens=q.split(/\s+/),exact=/^#\d+$/.test(q)?q.slice(1):null;let matches=0;for(const r of rows){const hay=(r[1]+' '+(canonical?r[3].join(' '):'')).toLocaleLowerCase();if(!(exact?String(r[0])===exact:tokens.every(t=>hay.includes(t))))continue;matches++;if(matches>100)continue;const e=text(out,'div','','candidate');if(canonical){const b=text(e,'button','Use ID');b.onclick=()=>pick(r[0]);}text(e,'strong',r[1]);text(e,'div',`ID ${r[0]} · approximate usage ${r[2]}`,'muted');}const status=document.createElement('p');status.textContent=`${matches.toLocaleString()} matches${matches>100?'; showing first 100 — narrow the query':''}.`;out.prepend(status)},200)})}
search('search','search-results',D.catalogue,true);search('unmerged-search','unmerged-results',D.unmerged,false);render();if(!storageWorks)$('save-status').textContent='Browser storage unavailable: export before closing.';
</script></html>'''
