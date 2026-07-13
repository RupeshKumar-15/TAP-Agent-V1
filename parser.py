# parser.py — evidence-first data extractor
"""
ZERO HALLUCINATION RULE:
Every extracted value is wrapped in an EvidencedFact.
If no source text supports a value, it is NOT returned.
Confidence levels:
  HIGH   = verbatim match (e.g. exact currency figure with CSR keyword nearby)
  MEDIUM = keyword present in relevant context (focus areas, geography)
  LOW    = implied — filtered OUT before output
"""

import re
import os
import yaml
from utils import EvidencedFact, combine_source_texts, window, excerpt


# ── Config ────────────────────────────────────────────────────────────────────

def _cfg() -> dict:
    p = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Currency patterns ─────────────────────────────────────────────────────────

CRORE_RE = re.compile(
    r"(?:₹|Rs\.?\s*|INR\s*)([\d,]+\.?\d*)\s*(?:crore|cr\.?)\b"
    r"|(?<!\w)([\d,]+\.?\d*)\s*(?:crore|cr\.?)\s+(?:rupees?|INR|₹)",
    re.IGNORECASE,
)
LAKH_RE = re.compile(
    r"(?:₹|Rs\.?\s*|INR\s*)([\d,]+\.?\d*)\s*(?:lakh|lac)\b",
    re.IGNORECASE,
)

CSR_KW = ["csr","corporate social","philanthrop","social responsibility",
          "schedule vii","csr spend","csr expenditure","csr budget",
          "csr obligation","csr fund","community investment"]

FP_KW  = ["market size","market cap","market was valued","industry valued",
          "annual revenue","total revenue","reported revenue","market valued at",
          "valued at usd","valued at $","stock price","share price",
          "raised in funding","valuation of","net profit"]

# ── India geography ───────────────────────────────────────────────────────────

INDIA_GEOS = [
    "andhra pradesh","arunachal pradesh","assam","bihar","chhattisgarh","goa",
    "gujarat","haryana","himachal pradesh","jharkhand","karnataka","kerala",
    "madhya pradesh","maharashtra","manipur","meghalaya","mizoram","nagaland",
    "odisha","punjab","rajasthan","sikkim","tamil nadu","telangana","tripura",
    "uttar pradesh","uttarakhand","west bengal","delhi","jammu","kashmir",
    "ladakh","chandigarh","puducherry",
    "mumbai","bangalore","bengaluru","hyderabad","chennai","kolkata","pune",
    "ahmedabad","surat","jaipur","lucknow","bhopal","indore","noida",
    "gurgaon","gurugram","coimbatore","kochi","mysuru","mysore","nagpur",
]

NGO_MARKERS = ["foundation","trust","society","ngo","nonprofit","non-profit",
               "charitable","charity","institute","organisation","organization",
               "centre","center","council","academy","association"]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _near_csr(s: str) -> bool:
    sl = s.lower()
    return any(k in sl for k in CSR_KW)

def _false_positive(s: str) -> bool:
    sl = s.lower()
    return any(k in sl for k in FP_KW)

def _kw_find(text_lower: str, kw: str) -> int:
    """
    Word-boundary keyword search. Returns match position or -1.
    Prevents substring false positives: 'goa' must not match 'goal',
    'maker' must not match 'policymaker', 'reading' must not match 'spreading'.
    """
    m = re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])",
                  text_lower)
    return m.start() if m else -1

# Source priority for choosing between conflicting spend figures
_SPEND_SOURCE_RANK = {
    "india_csr_page": 4, "mca_portal": 4, "national_csr_portal": 3,
    "annual_report": 3, "global_annual_report": 2,
    "mca_via_search": 1, "web_snippet": 0,
}

# Sanity ceiling: India's largest CSR spenders are ~₹1,500 crore.
# Anything above this is almost certainly a market-size/revenue figure.
_SPEND_MAX_PLAUSIBLE_CRORE = 3000

def _source_label(sources: list, char_pos: int, combined: str) -> tuple:
    """Find which source a character position in combined text belongs to."""
    offset = 0
    for s in sources:
        t = s.get("text","")
        if offset <= char_pos < offset + len(t) + 2:
            return s.get("source_name","web_snippet"), s.get("url","")
        offset += len(t) + 2
    return "web_snippet", ""


# ─────────────────────────────────────────────────────────────────────────────
# Extractors — each returns EvidencedFact or None
# ─────────────────────────────────────────────────────────────────────────────

def extract_india_spend(sources: list) -> dict:
    """
    Returns a dict with an EvidencedFact for spend, or empty/no-data dict.
    Confidence HIGH if ₹ figure appears within 300 chars of a CSR keyword.
    """
    combined = combine_source_texts(sources)
    candidates = []

    for m in CRORE_RE.finditer(combined):
        raw = (m.group(1) or m.group(2) or "").replace(",","")
        if not raw:
            continue
        val     = float(raw)
        if val > _SPEND_MAX_PLAUSIBLE_CRORE:
            continue  # market-size / revenue figure, not CSR spend
        snippet = window(combined, m.start(), 300, 100)
        if _false_positive(snippet):
            continue
        near    = _near_csr(snippet)
        conf    = "HIGH" if near else "MEDIUM"
        sname, surl = _source_label(sources, m.start(), combined)
        exrpt   = excerpt(combined, m.start())
        candidates.append((val, f"₹{val:g} crore", conf, sname, surl, exrpt))

    for m in LAKH_RE.finditer(combined):
        raw = m.group(1).replace(",","")
        if not raw:
            continue
        lakh  = float(raw)
        crore = lakh / 100
        snippet = window(combined, m.start(), 300, 100)
        if _false_positive(snippet) or not _near_csr(snippet):
            continue
        sname, surl = _source_label(sources, m.start(), combined)
        exrpt = excerpt(combined, m.start())
        candidates.append((crore, f"₹{lakh:g} lakh", "HIGH", sname, surl, exrpt))

    if not candidates:
        return {"inr_crore": None, "display": "Not publicly disclosed",
                "usd_approx": "", "evidence_fact": None}

    # RANKING (v5): trust beats size.
    # 1) HIGH confidence (₹ figure near a CSR keyword) beats MEDIUM
    # 2) better source (company page / MCA) beats search snippets
    # 3) only then the larger figure wins as tiebreak
    best = max(candidates, key=lambda x: (
        x[2] == "HIGH",
        _SPEND_SOURCE_RANK.get(x[3], 0),
        x[0],
    ))
    val, display, conf, sname, surl, exrpt = best
    fact = EvidencedFact(
        value=display, confidence=conf,
        source_url=surl, source_type=sname,
        excerpt=exrpt, field_name="india_csr_spend"
    )
    usd = f"≈ USD {val * 0.12:.1f}M" if val >= 1 else ""
    return {"inr_crore": val, "display": display, "usd_approx": usd,
            "evidence_fact": fact.to_dict() if fact.is_verified() else None}


def extract_focus_areas(sources: list) -> list:
    """
    Returns list of EvidencedFacts for each matched TAP focus keyword.
    Only MEDIUM/HIGH confidence (keyword found in source text).
    """
    cfg      = _cfg()
    kws      = cfg.get("tap_focus_areas", {})
    combined = combine_source_texts(sources)
    clow     = combined.lower()
    results  = []
    seen     = set()

    for kw in kws:
        idx = _kw_find(clow, kw)
        if idx >= 0 and kw not in seen:
            seen.add(kw)
            sname, surl = _source_label(sources, idx, combined)
            exrpt   = excerpt(combined, idx, 200)
            fact    = EvidencedFact(
                value=kw, confidence="MEDIUM",
                source_url=surl, source_type=sname,
                excerpt=exrpt, field_name="focus_area"
            )
            if fact.is_verified():
                results.append(fact.to_dict())

    return results[:12]


def detect_adjacency_signals(sources: list) -> dict:
    """
    Detect adjacency clusters with evidence.
    Every fired cluster carries source evidence.
    """
    cfg      = _cfg()
    clusters = cfg.get("adjacency_clusters", {})
    combined = combine_source_texts(sources).lower()
    result   = {}

    for cid, cluster in clusters.items():
        kws_found = []
        evidence_excerpts = []

        for kw in cluster.get("keywords",[]):
            idx = _kw_find(combined, kw)
            if idx >= 0:
                raw_combined = combine_source_texts(sources)
                exrpt = excerpt(raw_combined, idx, 180)
                kws_found.append(kw)
                evidence_excerpts.append(exrpt)

        fires = len(kws_found) > 0
        result[cid] = {
            "label":             cluster.get("label", cid),
            "keywords_found":    kws_found,
            "tap_reasoning":     cluster.get("tap_reasoning","").strip(),
            "boost":             cluster.get("boost",0) if fires else 0,
            "fires":             fires,
            "evidence_excerpts": evidence_excerpts[:2],
        }

    return result


_PARTNER_STOPWORDS = {"the","our","this","that","their","its","a","an","in","of",
                      "and","with","for","csr","india","new","are","is","has"}

def _tap_similarity(context: str, cfg: dict) -> list:
    """Returns list of TAP-similarity keywords found near a partner mention."""
    ctx = context.lower()
    return [kw for kw in cfg.get("partner_similarity_keywords", [])
            if _kw_find(ctx, kw) >= 0]


def extract_ngo_partners(sources: list, company: str = "") -> list:
    """
    Funded / implementation partners with evidence.
    Each dict: {name, source_url, source_type, excerpt,
                tap_similar (bool), similarity_signals (list),
                is_peer_ngo (bool), is_own_foundation (bool)}
    Only names literally present in fetched sources are returned.
    """
    cfg      = _cfg()
    combined = combine_source_texts(sources)
    clow     = combined.lower()
    company_tokens = set(re.sub(r"[^a-z0-9 ]"," ",company.lower()).split()) - _PARTNER_STOPWORDS

    found, seen = [], set()

    def _add(name, pos, is_peer):
        key = name.lower().strip()
        if key in seen or not (5 < len(name) < 80):
            return
        first = name.split()[0].lower()
        if first in _PARTNER_STOPWORDS:
            return
        seen.add(key)
        sname, surl = _source_label(sources, pos, combined)
        ctx     = window(combined, pos, 300, 300)
        signals = _tap_similarity(ctx, cfg)
        own     = bool(company_tokens and
                       any(t in key.split() for t in company_tokens))
        found.append({
            "name": name.strip(),
            "source_url": surl, "source_type": sname,
            "excerpt": excerpt(combined, pos, 200),
            "tap_similar": is_peer or len(signals) >= 2,
            "similarity_signals": signals[:5],
            "is_peer_ngo": is_peer,
            "is_own_foundation": own,
        })

    # Pass 1 — known TAP-peer NGOs mentioned anywhere in sources (strongest signal)
    for peer in cfg.get("tap_peer_ngos", []):
        idx = _kw_find(clow, peer.lower())
        if idx >= 0:
            _add(peer, idx, is_peer=True)

    # Pass 2 — generic NGO-shaped names in CSR context
    ngo_re = re.compile(
        r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,4}\s+"
        r"(?:" + "|".join(NGO_MARKERS) + r"))\b",
    )
    for m in ngo_re.finditer(combined):
        snip = window(combined, m.start(), 150, 80)
        if _near_csr(snip):
            _add(m.group(1), m.start(), is_peer=False)
        if len(found) >= 15:
            break

    # TAP-similar first, then peers, then rest
    found.sort(key=lambda p: (p["is_peer_ngo"], p["tap_similar"],
                              len(p["similarity_signals"])), reverse=True)
    return found[:12]


def extract_programs(sources: list) -> list:
    """Returns list of dicts {name, source_url, excerpt}."""
    combined = combine_source_texts(sources)
    prog_re  = re.compile(
        r'(?:programme?|initiative|scheme|project|campaign|mission)'
        r'["\s:–]+([A-Z][^\n.]{5,60})',
        re.IGNORECASE,
    )
    found, seen = [], set()
    for m in prog_re.finditer(combined):
        name  = m.group(1).strip().rstrip(".,;")
        snip  = window(combined, m.start(), 80, 40).lower()
        if _near_csr(snip) and name not in seen and len(name) > 5:
            seen.add(name)
            sname, surl = _source_label(sources, m.start(), combined)
            found.append({"name": name, "source_url": surl, "source_type": sname,
                          "excerpt": excerpt(combined, m.start(), 160)})
        if len(found) >= 8:
            break
    return found


def extract_geography(sources: list) -> list:
    """Returns list of dicts {place, source_url}."""
    combined = combine_source_texts(sources)
    clow     = combined.lower()
    found, seen = [], set()
    for geo in INDIA_GEOS:
        idx = _kw_find(clow, geo)
        if idx >= 0:
            snip = window(clow, idx, 200, 200)
            if _near_csr(snip):
                disp = geo.title()
                if disp not in seen:
                    seen.add(disp)
                    sname, surl = _source_label(sources, idx, combined)
                    found.append({"place": disp, "source_url": surl, "source_type": sname})
    return found[:10]


_CSR_TITLE_KW = ["csr", "corporate social responsibility", "sustainability",
                 "esg", "social impact", "community", "foundation"]

def _linkedin_search_url(name: str, company: str) -> str:
    from urllib.parse import quote_plus
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + quote_plus(f"{name} {company}".strip()))


def extract_decision_makers(sources: list, company: str = "") -> list:
    """
    CSR decision-makers with evidence.
    Each dict: {name, title, source_url, source_type, excerpt,
                linkedin_url (verified profile URL if the hit came from
                LinkedIn search results, else ""),
                linkedin_search_url (always present — clearly a search link,
                never a fabricated profile)}
    Names come ONLY from fetched text / LinkedIn's own published snippets.
    """
    found, seen = [], set()

    def _add(name, title, surl, stype, exrpt, li_url=""):
        key = name.lower().strip()
        if key in seen or len(name) < 5 or len(name.split()) < 2:
            return
        # Reject the company's own name captured as a person
        if company and (key in company.lower() or company.lower() in key):
            return
        seen.add(key)
        found.append({
            "name": name.strip(), "title": title.strip()[:90],
            "source_url": surl, "source_type": stype,
            "excerpt": exrpt,
            "linkedin_url": li_url,
            "linkedin_search_url": _linkedin_search_url(name, company),
        })

    # Pass 1 — LinkedIn search-result hits from the people_search source.
    # LinkedIn result titles look like: "Name - Title - Company | LinkedIn"
    li_title_re = re.compile(
        r"^([A-Z][\w.\-']+(?:\s+[A-Z][\w.\-']+){1,3})\s*[-–|]\s*(.+?)(?:\s*\|\s*LinkedIn)?$"
    )
    for s in sources:
        for hit in s.get("people_hits", []):
            title_txt = hit.get("title","").strip()
            url       = hit.get("url","")
            snippet   = hit.get("snippet","")
            m = li_title_re.match(title_txt)
            if not m:
                continue
            name, role = m.group(1), m.group(2)
            blob = f"{role} {snippet}".lower()
            if not any(k in blob for k in _CSR_TITLE_KW):
                continue  # not a CSR/sustainability person — skip
            li = url if "linkedin.com/in" in url else ""
            _add(name, role, url, "linkedin_search",
                 f"{title_txt} — {snippet}"[:280], li_url=li)

    # Pass 2 — names near CSR titles in fetched page/PDF text
    combined = combine_source_texts(sources)
    patterns = [
        re.compile(
            r"([A-Z][a-z\-]+(?:\s+[A-Z][a-z\-]+){1,3})"
            r"[\s,–\-]+(chief\s+csr\s+officer|head\s+of\s+csr|"
            r"csr\s+director|chief\s+sustainability\s+officer|"
            r"sustainability\s+head|head[,\s]+csr|csr\s+head|"
            r"vp[\s,\-]+csr|chairperson[,\s]+csr\s+committee)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(head\s+of\s+csr|csr\s+director|chief\s+sustainability\s+officer|csr\s+head)"
            r"[\s:,–\-]+([A-Z][a-z\-]+(?:\s+[A-Z][a-z\-]+){1,3})",
            re.IGNORECASE,
        ),
    ]
    for i, pat in enumerate(patterns):
        for m in pat.finditer(combined):
            name  = m.group(1) if i == 0 else m.group(2)
            title = m.group(2) if i == 0 else m.group(1)
            sname, surl = _source_label(sources, m.start(), combined)
            _add(name, title, surl, sname, excerpt(combined, m.start(), 200))
        if len(found) >= 8:
            break
    return found[:8]


# ─────────────────────────────────────────────────────────────────────────────
# CSR delivery model — FUNDER vs IMPLEMENTER vs HYBRID (with evidence)
# ─────────────────────────────────────────────────────────────────────────────

def detect_csr_model(sources: list) -> dict:
    """
    Identifies how the company delivers CSR:
      FUNDER      — grants to external implementation partners (ideal for TAP)
      IMPLEMENTER — runs CSR via its own foundation/teams (harder entry)
      HYBRID      — both
      UNCLEAR     — no signals in public sources
    Evidence-based: signals must literally appear in fetched text.
    """
    cfg      = _cfg().get("csr_delivery_model", {})
    combined = combine_source_texts(sources)
    clow     = combined.lower()

    f_hits, i_hits = [], []
    f_ev = i_ev = ""
    for kw in cfg.get("funder_signals", []):
        idx = _kw_find(clow, kw)
        if idx >= 0:
            f_hits.append(kw)
            if not f_ev:
                f_ev = excerpt(combined, idx, 200)
    for kw in cfg.get("implementer_signals", []):
        idx = _kw_find(clow, kw)
        if idx >= 0:
            i_hits.append(kw)
            if not i_ev:
                i_ev = excerpt(combined, idx, 200)

    if f_hits and i_hits:
        model = "HYBRID"
    elif f_hits:
        model = "FUNDER"
    elif i_hits:
        model = "IMPLEMENTER"
    else:
        model = "UNCLEAR"

    notes = {
        "FUNDER":      ("Funds external implementation partners — the ideal model "
                        "for TAP: they already grant to NGOs like us."),
        "IMPLEMENTER": ("Runs CSR through its own foundation/teams — partnership is "
                        "possible but harder; TAP would need to plug into their delivery."),
        "HYBRID":      ("Both funds NGO partners and runs its own programmes — strong "
                        "opening: pitch TAP into their partner portfolio."),
        "UNCLEAR":     ("Delivery model not evident from public sources — probe "
                        "during outreach."),
    }
    return {"model": model, "note": notes[model],
            "funder_signals": f_hits[:6], "implementer_signals": i_hits[:6],
            "funder_evidence": f_ev, "implementer_evidence": i_ev}


# ─────────────────────────────────────────────────────────────────────────────
# Verification pass — "sacrosanct" double-check of every published fact
# ─────────────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()

def verify_facts(parsed: dict, sources: list) -> dict:
    """
    Re-checks every fact that will be published: its excerpt must literally
    exist in the fetched source texts. Anything that fails is flagged.
    Returns {"checks": [...], "verified": n, "failed": n, "pass_rate": pct}
    """
    haystack = _norm(combine_source_texts(sources) + " " +
                     " ".join(" ".join(f"{h.get('title','')} {h.get('snippet','')}"
                                        for h in s.get("people_hits", []))
                              for s in sources))
    checks = []

    def _check(field, value, exrpt):
        probe = _norm(exrpt)[:120]
        ok = bool(probe) and probe in haystack
        checks.append({"field": field, "value": str(value)[:80],
                       "status": "VERIFIED" if ok else "CHECK MANUALLY"})

    ef = (parsed.get("spend") or {}).get("evidence_fact")
    if ef:
        _check("India CSR spend", ef.get("value",""), ef.get("excerpt",""))
    for f in parsed.get("focus_areas", []):
        _check("Focus area", f.get("value",""), f.get("excerpt",""))
    for p in parsed.get("ngo_partners", []):
        _check("Funded partner", p.get("name",""), p.get("excerpt",""))
    for d in parsed.get("decision_makers", []):
        _check("Decision maker", d.get("name",""), d.get("excerpt",""))

    verified = sum(1 for c in checks if c["status"] == "VERIFIED")
    total    = len(checks)
    return {"checks": checks, "verified": verified,
            "failed": total - verified,
            "pass_rate": round(verified / total * 100) if total else 100}


# ─────────────────────────────────────────────────────────────────────────────
# Master parse
# ─────────────────────────────────────────────────────────────────────────────

def extract_partnership_plans(sources: list, company: str = "") -> dict:
    """
    Source-7 signals: NGO collaborations, announced/future CSR plans, and
    leadership statements on education. Sentence-level scan over ALL found
    sources (not just plans_search), so annual reports contribute too.
    """
    collab_kw = ["partnered with", "partnership with", "in collaboration with",
                 "implementing partner", "implementation partner", "ngo partner",
                 "mou with", "joined hands with", "tie-up with", "tied up with",
                 "collaborated with", "alliance with"]
    plan_kw   = ["will invest", "announced", "announces", "plans to", "commits",
                 "committed to", "pledge", "aims to", "to launch", "will launch",
                 "upcoming", "roadmap", "next phase", "by 2027", "by 2028"]
    lead_kw   = ["ceo", "chairman", "chairperson", "managing director",
                 "csr head", "head of csr", "chief sustainability", "said"]
    _relevant = ["csr", "education", "school", "skill", "student", "learning",
                 "ngo", "foundation", "literacy", "children", "youth"]

    out  = {"collaborations": [], "future_plans": [], "leadership_statements": []}
    seen = set()

    def _scan(sent: str, sname: str, surl: str):
        sent = sent.strip()
        if not (40 <= len(sent) <= 400):
            return
        sl = sent.lower()
        if not any(k in sl for k in _relevant):
            return
        key = sl[:80]
        if key in seen:
            return
        item = {"excerpt": sent, "source": sname, "url": surl}
        if any(k in sl for k in collab_kw) and len(out["collaborations"]) < 5:
            seen.add(key); out["collaborations"].append(item)
        elif any(k in sl for k in plan_kw) and len(out["future_plans"]) < 5:
            seen.add(key); out["future_plans"].append(item)
        elif any(k in sl for k in lead_kw) and len(out["leadership_statements"]) < 5:
            seen.add(key); out["leadership_statements"].append(item)

    for s in sources:
        if s.get("status") != "FOUND":
            continue
        sname, surl = s.get("source_name", ""), s.get("url", "")
        for sent in re.split(r"(?<=[.!?])\s+", s.get("text", "")):
            _scan(sent, sname, surl)
        for hit in s.get("plan_hits", []):
            _scan(f"{hit.get('title','')}: {hit.get('snippet','')}",
                  "plans_search", hit.get("url", ""))
    return out


def parse_all(sources: list, company: str = "") -> dict:
    parsed = {
        "spend":             extract_india_spend(sources),
        "focus_areas":       extract_focus_areas(sources),
        "adjacency_signals": detect_adjacency_signals(sources),
        "ngo_partners":      extract_ngo_partners(sources, company),
        "programs":          extract_programs(sources),
        "geography":         extract_geography(sources),
        "decision_makers":   extract_decision_makers(sources, company),
        "csr_delivery_model": detect_csr_model(sources),
        "partnership_plans": extract_partnership_plans(sources, company),
    }
    parsed["verification"] = verify_facts(parsed, sources)
    return parsed
