# webui.py — server-rendered UI in the fork's report design (teal / Inter /
# panel layout), carrying our evidence-first sections as first-class panels.
# Stateless by design: download files are embedded as base64 data URLs.
import html as _html

_CSS = """
:root{--ink:#111114;--ink-soft:#33403F;--grey:#6B7280;--grey-light:#9CA8A7;
  --line:#E1E9E7;--line-soft:#EDF4F2;--bg:#FFFFFF;--panel:#F7FAF9;
  --accent:#0F3D3E;--accent-mid:#146B65;--yellow:#F5C518;
  --good:#1E7A46;--good-soft:#EAF6EF;--warn:#8A6200;--warn-soft:#FBF3DF;
  --bad:#A32626;--bad-soft:#FBEAEA;--radius:10px;--radius-sm:7px;
  --shadow-sm:0 1px 2px rgba(15,61,62,0.06);--shadow-md:0 2px 10px rgba(15,61,62,0.08)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
  background:var(--bg);color:var(--ink);line-height:1.6;font-size:15px;
  -webkit-font-smoothing:antialiased}
main{max-width:1040px;margin:0 auto;padding:48px 24px 96px}
.hero{max-width:560px;margin:0 auto;text-align:center;padding:40px 0 32px}
.hero h1{font-size:26px;font-weight:700;letter-spacing:-0.3px;color:var(--accent)}
.subtitle{color:var(--grey);font-size:14px;margin:8px 0 32px}
.search-form{display:flex;flex-direction:column;align-items:center;gap:16px}
.search-field{width:100%;display:flex;align-items:center;gap:10px;
  padding:3px 6px 3px 18px;border:1px solid var(--line);border-radius:999px;
  background:var(--bg);transition:border-color .15s,box-shadow .15s}
.search-field:focus-within{border-color:var(--accent-mid);
  box-shadow:0 0 0 3px rgba(20,107,101,0.12)}
.search-field input{flex:1;border:none;outline:none;font-size:15px;padding:12px 0;
  font-family:inherit;background:transparent}
.search-field button{border:none;border-radius:999px;background:var(--accent);
  color:#fff;font-weight:600;font-size:14px;padding:11px 22px;cursor:pointer}
.modes{display:flex;gap:10px;justify-content:center}
.modes label{border:1px solid var(--line);border-radius:999px;padding:7px 16px;
  font-size:13px;color:var(--ink-soft);cursor:pointer;user-select:none}
.modes label:has(input:checked){border-color:var(--accent-mid);
  background:var(--good-soft);color:var(--accent)}
.modes input{margin-right:6px}
.report-head{margin:8px 0 20px}
.eyebrow{font-size:12px;font-weight:600;letter-spacing:0.08em;
  text-transform:uppercase;color:var(--accent-mid)}
.report-head h1{font-size:28px;font-weight:700;letter-spacing:-0.4px;
  color:var(--ink);margin-top:4px}
.status-pill{display:inline-block;margin-left:12px;border-radius:999px;
  padding:4px 14px;font-size:12.5px;font-weight:700;vertical-align:middle}
.status-good{background:var(--good-soft);color:var(--good)}
.status-mid{background:var(--warn-soft);color:var(--warn)}
.status-low{background:var(--bad-soft);color:var(--bad)}
.summary-block{display:flex;gap:26px;align-items:flex-start;background:var(--panel);
  border:1px solid var(--line-soft);border-radius:var(--radius);padding:22px 24px;
  box-shadow:var(--shadow-sm);margin-bottom:16px}
.score-figure{display:flex;align-items:baseline;gap:4px;min-width:130px}
.score-figure-num{font-size:52px;font-weight:800;color:var(--accent);line-height:1}
.score-figure-max{font-size:15px;color:var(--grey-light);font-weight:600}
.summary-tier{display:inline-block;font-weight:700;font-size:14px;
  color:var(--tier-color,var(--accent));margin-bottom:6px}
.summary-sub{font-size:12px;color:var(--grey);margin-bottom:8px}
.summary-insight{font-size:14px;color:var(--ink-soft)}
.meta-strip{display:flex;flex-wrap:wrap;gap:0;border:1px solid var(--line);
  border-radius:var(--radius);overflow:hidden;margin-bottom:16px}
.meta-strip-item{flex:1;min-width:150px;padding:12px 16px;border-right:1px solid var(--line-soft)}
.meta-strip-item:last-child{border-right:none}
.meta-strip-label{display:block;font-size:11.5px;color:var(--grey);
  text-transform:uppercase;letter-spacing:0.05em}
.meta-strip-val{font-size:15px;font-weight:700;color:var(--accent)}
.note{display:flex;gap:10px;align-items:flex-start;border-radius:var(--radius-sm);
  padding:11px 14px;font-size:13.5px;margin-bottom:10px}
.note-good{background:var(--good-soft);color:var(--good)}
.note-warn{background:var(--warn-soft);color:var(--warn)}
.note-bad{background:var(--bad-soft);color:var(--bad)}
.section{margin:26px 0}
.section h2{font-size:16px;font-weight:700;color:var(--accent);margin-bottom:10px;
  letter-spacing:-0.2px}
.section h3{font-size:13.5px;font-weight:700;color:var(--ink-soft);margin:14px 0 6px}
.panel{background:var(--panel);border:1px solid var(--line-soft);
  border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow-sm);
  font-size:14px}
table{width:100%;border-collapse:collapse;font-size:13.5px;background:var(--bg);
  border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
th{background:var(--panel);text-align:left;font-size:12px;color:var(--grey);
  text-transform:uppercase;letter-spacing:0.04em}
th,td{padding:9px 12px;border-bottom:1px solid var(--line-soft);vertical-align:top}
tr:last-child td{border-bottom:none}
.hl{background:var(--yellow);color:var(--ink);border-radius:3px;
  padding:0 4px;font-weight:600}
.cite{color:var(--accent-mid);font-size:12px;text-decoration:none;
  border-bottom:1px dashed var(--accent-mid)}
.evidence{border-left:3px solid var(--line);padding:4px 10px;margin:6px 0;
  color:var(--grey);font-size:12.5px;font-style:italic}
.small{font-size:12.5px;color:var(--grey)}
.dl{display:inline-block;margin:8px 10px 0 0;padding:10px 18px;
  border:1px solid var(--accent-mid);border-radius:var(--radius-sm);
  color:var(--accent);font-weight:600;font-size:13.5px;text-decoration:none}
.dl:hover{background:var(--good-soft)}
details{border:1px solid var(--line);border-radius:var(--radius);
  padding:10px 14px;margin:10px 0;background:var(--bg)}
summary{cursor:pointer;font-weight:700;font-size:13.5px;color:var(--accent)}
a.back{color:var(--grey);font-size:13px;text-decoration:none}
.footer{margin-top:40px;font-size:12px;color:var(--grey-light);
  border-top:1px solid var(--line-soft);padding-top:14px}
.loading-joke{text-align:center;margin:26px 0 18px;font-size:14px;
  color:var(--accent-mid);font-weight:600;min-height:22px}
.skeleton-box{background:linear-gradient(90deg,var(--line-soft) 25%,#fff 50%,
  var(--line-soft) 75%);background-size:200% 100%;
  animation:shimmer 1.4s infinite;border-radius:6px}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.skeleton-scorecard{display:flex;gap:20px;margin:14px 0}
.skeleton-scorebox{width:130px;height:90px}
.skeleton-lines{flex:1;display:flex;flex-direction:column;gap:10px;padding-top:6px}
.skeleton-line{height:12px}
.skeleton-heading{height:14px;margin:22px 0 10px}
.skeleton-bars{display:flex;flex-direction:column;gap:12px}
.skeleton-bar{display:flex;align-items:center;gap:14px}
.skeleton-table{height:140px}
.skeleton-hint{margin-top:18px;font-size:12.5px;color:var(--grey);text-align:center}
"""

_HOME_SCRIPT = """
<script>
  var form=document.querySelector('form.search-form');
  var h=document.createElement('input');h.type='hidden';h.name='mode';h.value='screen';
  form.appendChild(h);
  document.querySelectorAll('.modes input').forEach(function(r){
    r.addEventListener('change',function(){h.value=r.value;});
  });

  var screenLines=[
    "You give it a search, we do the rest...",
    "Working hard so you can sit back and watch...",
    "Meanwhile, you have done absolutely nothing. Impressive...",
    "5 star. Do nothing. That's the tagline...",
    "We fetch, we read, we score. You blink...",
    "Somewhere, someone is doing your job for you...",
    "You: chai break. Us: reading annual reports...",
    "Go on, give us 5 stars, you have earned it by doing nothing..."
  ];
  var deepLines=[
    "You give it a search, we do the rest...",
    "Seven sources deep. Your effort level: zero...",
    "5 star. Do nothing. That's the tagline...",
    "Deep research, shallow effort from you, we are not judging...",
    "We are doing the reading. You are doing the scrolling...",
    "Somewhere, someone is doing your job for you...",
    "This is the part where you take credit later...",
    "Go on, give us 5 stars, you have earned it by doing nothing..."
  ];

  function skeletonHTML(company,mode){
    var hint = mode==="deep"
      ? "Running deep research on "+company+" across seven sources — this can take 2\\u20134 minutes."
      : "Screening "+company+" on the fastest sources first — usually under a minute.";
    return '<div class="loading-joke"><span id="joke"></span></div>'
      +'<div class="skeleton-scorecard">'
      +'<div class="skeleton-box skeleton-scorebox"></div>'
      +'<div class="skeleton-lines">'
      +'<div class="skeleton-box skeleton-line" style="width:92%"></div>'
      +'<div class="skeleton-box skeleton-line" style="width:78%"></div>'
      +'<div class="skeleton-box skeleton-line" style="width:85%"></div>'
      +'</div></div>'
      +'<div class="skeleton-box skeleton-heading" style="width:180px"></div>'
      +'<div class="skeleton-bars">'
      +'<div class="skeleton-bar"><div class="skeleton-box" style="width:120px;height:11px"></div><div class="skeleton-box" style="flex:1;height:7px"></div></div>'
      +'<div class="skeleton-bar"><div class="skeleton-box" style="width:140px;height:11px"></div><div class="skeleton-box" style="flex:1;height:7px"></div></div>'
      +'<div class="skeleton-bar"><div class="skeleton-box" style="width:100px;height:11px"></div><div class="skeleton-box" style="flex:1;height:7px"></div></div>'
      +'<div class="skeleton-bar"><div class="skeleton-box" style="width:150px;height:11px"></div><div class="skeleton-box" style="flex:1;height:7px"></div></div>'
      +'</div>'
      +'<div class="skeleton-box skeleton-heading" style="width:260px"></div>'
      +'<div class="skeleton-box skeleton-table"></div>'
      +'<div class="skeleton-hint">'+hint+'</div>';
  }

  form.addEventListener('submit',function(){
    var b=form.querySelector('button');
    b.textContent='Researching\\u2026';b.disabled=true;
    var company=(form.querySelector('[name=company]').value||'the company');
    var mode=h.value;

    // Hide the hero + info panel (keep the form in the DOM so the POST
    // proceeds), then show the fork-style skeleton while the browser waits
    // for the synchronous response.
    document.querySelectorAll('main > *').forEach(function(el){
      el.style.display='none';
    });
    var sk=document.createElement('div');
    sk.innerHTML=skeletonHTML(company,mode);
    document.querySelector('main').appendChild(sk);

    var lines = mode==='deep' ? deepLines : screenLines;
    var el=document.getElementById('joke');
    var i=0; el.textContent=lines[0];
    setInterval(function(){
      if(!document.body.contains(el)) return;
      i=(i+1)%lines.length;
      el.textContent=lines[i];
    },2200);
  });
</script>
"""


def _e(s):
    return _html.escape(str(s or ""))


def _hl(text) -> str:
    """Escape, then render the analyst's **highlight** markers as .hl marks."""
    s = _e(text)
    out, parts = "", s.split("**")
    for i, p in enumerate(parts):
        out += f"<span class='hl'>{p}</span>" if i % 2 else p
    return out


def _cite(url, label="source") -> str:
    if not url:
        return ""
    return f" <a class='cite' href='{_e(url)}' target='_blank' rel='noopener'>{_e(label)}</a>"


def _note(kind, inner) -> str:
    icon = "✓" if kind == "good" else "!"
    return f"<div class='note note-{kind}'><span>{icon}</span><span>{inner}</span></div>"


def _page(body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>TAP CSR Research Agent</title><style>{_CSS}</style></head>"
            f"<body><main>{body}</main></body></html>")


def render_home(error: str = "") -> str:
    err = f"<p style='color:var(--bad);text-align:center'>{_e(error)}</p>" if error else ""
    body = ("""
<div class="hero">
  <h1>TAP CSR Research Agent</h1>
  <p class="subtitle">Evidence-first corporate funder research for The
  Apprentice Project — official sources only, every claim cited.</p>
""" + err + """
  <form class="search-form" method="post" action="/research">
    <div class="search-field">
      <input type="text" name="company" placeholder="Company name — e.g. Capgemini, HCL Technologies" required>
      <button type="submit">Research</button>
    </div>
    <div class="modes">
      <label><input type="radio" name="mode_pick" value="screen" checked>Prospect Screening · ~1 min</label>
      <label><input type="radio" name="mode_pick" value="deep">Deep Research · 2–4 min</label>
    </div>
  </form>
</div>
""" + _HOME_SCRIPT + """
<div class="panel" style="max-width:560px;margin:0 auto">
  <b>How it works.</b> <span class="small">The engine researches up to 7 official
  sources (company CSR page, MCA CSR-2, National CSR Portal, annual report,
  partners, decision-makers, announced plans), extracts every fact with a
  verbatim excerpt, scores fit with a Claude deep-read analysis (rule-engine
  fallback), and produces DOCX / HTML / XLSX / JSON reports.</span>
</div>""")
    return _page(body)


def _meta_strip(a: dict, bd: dict) -> str:
    if not a:
        return ""
    conf = 0
    crits = a.get("criteria") or []
    if crits:
        conf = round(sum(c.get("confidence", 0) for c in crits) / len(crits), 1)
    items = [
        ("Semantic alignment", f"{a.get('overall_semantic_alignment', 0)}/100"),
        ("Evidence authenticity", f"{a.get('overall_authenticity_score', 0)}/100"),
        ("Avg. criteria confidence", f"{conf}%"),
        ("Delivery model", _e(a.get("delivery_model", "UNCLEAR"))),
    ]
    cells = "".join(
        f"<div class='meta-strip-item'><span class='meta-strip-label'>{k}</span>"
        f"<span class='meta-strip-val'>{v}</span></div>" for k, v in items)
    return f"<div class='meta-strip'>{cells}</div>"


def _notes(a: dict) -> str:
    if not a:
        return ""
    out = ""
    for f in (a.get("red_flags") or [])[:5]:
        if not f.get("flag"):
            continue
        kind = "bad" if f.get("severity") == "high" else "warn"
        out += _note(kind, f"<b>{_e(f['flag'])}</b> ({_e(f.get('severity',''))}) — "
                            f"{_hl(f.get('explanation',''))}")
    el = a.get("eligibility") or {}
    if el.get("plausibly_mandated") == "UNLIKELY":
        out += _note("warn", "Section 135 CSR mandate looks unlikely to apply — "
                             + _hl(el.get("reasoning", "")))
    gf = a.get("group_foundation") or {}
    if gf.get("routed_through_group"):
        out += _note("warn", "CSR is likely routed through "
                     + _e(gf.get("foundation_name") or "a separate group foundation")
                     + " — " + _hl(gf.get("explanation", "")))
    rfp = a.get("rfp_signal") or {}
    if rfp.get("present"):
        out += _note("good", "Open outreach signal detected via "
                     + _e(rfp.get("channel", "")) + " — " + _hl(rfp.get("evidence", "")))
    return out


def _analysis_sections(a: dict) -> str:
    if not a:
        return ""
    out = ""
    if a.get("fit_rationale"):
        out += (f"<section class='section'><h2>Fit rationale</h2>"
                f"<div class='panel'>{_hl(a['fit_rationale'])}<br>"
                f"<span class='small'>{_hl(a.get('alignment_rationale',''))}</span>"
                f"</div></section>")

    sp = a.get("spend") or {}
    if sp.get("has_disclosed_budget") or sp.get("display"):
        hist = " · ".join(f"{_e(h.get('fiscal_year',''))}: {_e(h.get('display') or h.get('inr_crore'))}"
                          for h in (sp.get("history") or [])[:4]
                          if h.get("display") or h.get("inr_crore"))
        out += (f"<section class='section'><h2>CSR spend</h2><div class='panel'>"
                f"<b>{_e(sp.get('display') or '—')}</b> · trend "
                f"<b>{_e(sp.get('trend_direction','UNKNOWN'))}</b>"
                f"{(' · ' + hist) if hist else ''}<br>"
                f"<span class='small'>{_hl(sp.get('trend_evidence',''))}</span>"
                f"</div></section>")

    progs = [p for p in (a.get("programmes") or []) if p.get("name")][:8]
    if progs:
        rows = "".join(
            f"<tr><td><b>{_e(p['name'])}</b>{' · multi-year' if p.get('is_multi_year') else ''}</td>"
            f"<td class='small'>{_e(p.get('description',''))} {_e(p.get('cohort_or_scale',''))}</td></tr>"
            for p in progs)
        out += (f"<section class='section'><h2>Programmes</h2><table>"
                f"<tr><th>Programme</th><th>Detail</th></tr>{rows}</table></section>")

    partners = [p for p in (a.get("partners") or []) if p.get("name")][:8]
    if partners:
        rows = "".join(f"<tr><td>{_e(p['name'])}</td>"
                       f"<td class='small'>{_e(p.get('relationship_type',''))}</td></tr>"
                       for p in partners)
        out += (f"<section class='section'><h2>Partners (AI-read)</h2><table>"
                f"<tr><th>Organisation</th><th>Relationship</th></tr>{rows}</table></section>")

    dms = [d for d in (a.get("decision_makers") or []) if d.get("name")][:6]
    if dms:
        rows = "".join(f"<tr><td>{_e(d['name'])}</td><td class='small'>{_e(d.get('title',''))}</td>"
                       f"<td class='small'>{_e(d.get('tenure_status','UNKNOWN'))}</td></tr>"
                       for d in dms)
        out += (f"<section class='section'><h2>Decision-makers</h2><table>"
                f"<tr><th>Name</th><th>Title</th><th>Tenure</th></tr>{rows}</table></section>")

    cp = (a.get("contact_pathway") or {}).get("channel", "")
    if cp:
        out += (f"<section class='section'><h2>Contact pathway</h2>"
                f"<div class='panel'>{_hl(cp)}</div></section>")

    crit_rows = "".join(
        f"<tr><td>{_e(c.get('name',''))}</td>"
        f"<td style='text-align:center'><b>{c.get('score',0)}</b></td>"
        f"<td style='text-align:center' class='small'>{c.get('confidence',0)}%</td>"
        f"<td class='small'>{_hl(c.get('evidence',''))}</td></tr>"
        for c in (a.get("criteria") or []))
    if crit_rows:
        out += (f"<details><summary>Criteria scorecard — 17 signals (AI-scored 0–5)</summary>"
                f"<table style='margin-top:10px'><tr><th>Criterion</th><th>Score</th>"
                f"<th>Conf.</th><th>Evidence</th></tr>{crit_rows}</table></details>")

    oq = a.get("open_questions") or []
    if oq:
        items = "".join(f"<li>{_e(q)}</li>" for q in oq)
        out += (f"<section class='section'><h2>Open questions</h2>"
                f"<div class='panel'><ul style='padding-left:18px'>{items}</ul></div></section>")

    out += (f"<p class='small'>{_hl(a.get('source_quality_assessment',''))} "
            f"{_hl(a.get('evidence_recency',''))} {_hl(a.get('csr_head_note',''))}</p>")
    return out


def _evidence_sections(result: dict) -> str:
    """OUR evidence discipline: every statement traceable to a fetched
    excerpt — focus areas, adjacency clusters, partners, verification log."""
    data = result.get("data", {}) or {}
    bd   = result.get("breakdown", {}) or {}
    out  = "<section class='section'><h2>Evidence — every claim, cited</h2>"

    fa = data.get("focus_areas") or []
    if fa:
        out += "<h3>CSR focus areas found</h3>"
        for f in fa[:10]:
            out += (f"<div class='panel' style='margin-bottom:8px'><b>{_e(f.get('value',''))}</b>"
                    f"{_cite(f.get('source_url'), f.get('source_type','source'))}"
                    f"<div class='evidence'>“{_e((f.get('excerpt') or '')[:260])}”</div></div>")

    fired = bd.get("adjacency_boost", {}).get("fired_clusters") or []
    if fired:
        out += "<h3>Adjacency signals</h3>"
        for c in fired:
            ex = (c.get("evidence_excerpts") or [""])[0]
            out += (f"<div class='panel' style='margin-bottom:8px'><b>{_e(c.get('label',''))}</b>"
                    f" <span class='small'>keywords: {_e(', '.join(c.get('keywords_found', [])[:4]))}</span>"
                    f"{f'<div class=evidence>“{_e(ex[:240])}”</div>' if ex else ''}</div>")

    partners = data.get("ngo_partners") or []
    if partners:
        rows = "".join(
            f"<tr><td>{_e(p.get('name',''))}"
            f"{' <b>· PEER NGO</b>' if p.get('is_peer_ngo') else (' · TAP-similar' if p.get('tap_similar') else '')}</td>"
            f"<td class='small'>“{_e((p.get('excerpt') or '')[:160])}”</td></tr>"
            for p in partners[:8])
        out += (f"<h3>Funded / implementing partners (from fetched text)</h3>"
                f"<table><tr><th>Partner</th><th>Excerpt</th></tr>{rows}</table>")

    verif = data.get("verification", {}) or {}
    checks = verif.get("checks") or []
    if checks:
        rows = "".join(
            f"<tr><td>{_e(c.get('field',''))}</td><td>{_e(c.get('value',''))}</td>"
            f"<td><span class='status-pill status-"
            f"{'good' if c.get('status')=='VERIFIED' else ('mid' if 'CONTEXT' in c.get('status','') else 'low')}'"
            f" style='margin-left:0'>{_e(c.get('status',''))}</span></td></tr>"
            for c in checks[:20])
        out += (f"<h3>Verification log — {verif.get('verified',0)}/{len(checks)} "
                f"verified in context</h3><table><tr><th>Field</th><th>Value</th>"
                f"<th>Status</th></tr>{rows}</table>")

    links = [s for s in (result.get("sources") or []) if s.get("status") != "NOT_TRIED"]
    if links:
        rows = "".join(
            f"<tr><td>{_e(s.get('source_name',''))}</td><td>{_e(s.get('status',''))}</td>"
            f"<td>{_cite(s.get('url'), 'open') or '—'}</td></tr>" for s in links)
        out += (f"<h3>Sources checked</h3><table><tr><th>Source</th><th>Status</th>"
                f"<th>Link</th></tr>{rows}</table>")

    return out + "</section>"


def _methodology_section(meth: dict) -> str:
    tier = meth["tier"]
    rows = "".join(
        f"<tr><td>{_e(c['name'])}</td><td style='text-align:center'><b>{c['score']}</b></td>"
        f"<td>{_e(c['rating'])}</td><td class='small'>{_e(c['evidence'])}</td></tr>"
        for c in meth["criteria"])
    return (f"<details><summary>Methodology scorecard — 8 criteria (0–5): "
            f"{_e(meth['verdict_line'])}</summary>"
            f"<table style='margin-top:10px'><tr><th>Criterion</th><th>Score</th>"
            f"<th>Rating</th><th>Evidence</th></tr>{rows}</table>"
            f"<p class='small'>{_e(meth['csr_head_note'])}</p></details>")


def _engine_bars(bd: dict) -> str:
    labels = {"focus_alignment": "Focus alignment", "adjacency_boost": "Adjacency",
              "partner_similarity": "Partner similarity", "geography_fit": "Geography",
              "csr_maturity": "CSR maturity", "budget_size": "Budget",
              "source_quality": "Source quality"}
    rows = ""
    for dim, info in bd.items():
        if not (isinstance(info, dict) and "score" in info and "max" in info):
            continue
        pct = round(100 * info["score"] / info["max"]) if info["max"] else 0
        rows += (f"<tr><td>{labels.get(dim, _e(dim))}</td>"
                 f"<td><div style='background:var(--line-soft);border-radius:4px;height:8px'>"
                 f"<div style='width:{pct}%;background:var(--accent-mid);height:8px;"
                 f"border-radius:4px'></div></div></td>"
                 f"<td class='small'>{info['score']}/{info['max']}</td></tr>")
    if not rows:
        return ""
    return (f"<details><summary>Rule-engine diagnostics (fallback scorer)</summary>"
            f"<table style='margin-top:10px'>{rows}</table></details>")


def render_results(company: str, mode: str, result: dict, meth: dict,
                   files: dict) -> str:
    fit   = result.get("fit_score", 0)
    bd    = result.get("breakdown", {}) or {}
    a     = result.get("analysis")
    tier  = result.get("scoring_tier", {}) or {}
    src   = result.get("score_source", "deterministic_engine")
    eng   = result.get("engine_fit_score", fit)

    pill = ""
    if mode == "screen":
        cls = "good" if fit >= 80 else ("mid" if fit >= 45 else "low")
        lbl = "Qualify" if fit >= 80 else (tier.get("label", "") if fit >= 45 else "Skip")
        pill = f"<span class='status-pill status-{cls}'>{_e(lbl)}</span>"

    sub = ("Scored by Claude analysis" if src == "claude_analysis"
           else "Scored by rule engine")
    if eng != fit:
        sub += f" · engine {eng}/100"
    sub += f" · methodology {meth['average']}/5"

    dls = ""
    if files:
        links = "".join(
            f"<a class='dl' download='{_e(fn)}' href='data:{mime};base64,{b64}'>{_e(lbl)}</a>"
            for lbl, (fn, mime, b64) in files.items())
        dls = (f"<section class='section'><h2>Downloads</h2>{links}"
               f"<p class='small'>Files are embedded in this page — save them "
               f"before closing the tab.</p></section>")

    body = f"""
<a class="back" href="/">← New search</a>
<header class="report-head">
  <div><span class="eyebrow">{'Prospect Screening' if mode == 'screen' else 'Deep Research'}</span>{pill}</div>
  <h1>{_e(company)}</h1>
</header>

<section class="summary-block">
  <div class="score-figure">
    <div class="score-figure-num">{fit}</div>
    <div class="score-figure-max">/ 100</div>
  </div>
  <div>
    <div class="summary-tier" style="--tier-color:{_e(tier.get('color','#0F3D3E'))}">{_e(tier.get('label',''))}</div>
    <div class="summary-sub">{sub} · {_e(tier.get('action',''))}</div>
    <p class="summary-insight">{_hl(result.get('strategic_insight',''))}</p>
  </div>
</section>

{_meta_strip(a, bd)}
{_notes(a)}
{_analysis_sections(a)}
{_evidence_sections(result)}
{_methodology_section(meth)}
{_engine_bars(bd)}
{dls}
<div class="footer">TAP CSR Research Agent · fundraising@theapprenticeproject.org ·
Official sources only · every claim carries a citation · engine drafts, a person verifies.</div>
"""
    return _page(body)
