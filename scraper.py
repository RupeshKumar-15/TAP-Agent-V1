# scraper.py — verified multi-source fetcher with MCA deep-search
"""
Six sources, in priority order. NEW in v5:
  - Source 5: funded / implementation partners
  - Source 6: CSR decision-makers via LinkedIn search snippets
  - All fetches track their evidence for the verifier
  - Screen mode: only sources 1+4 (fast, ~30s)
  - Deep mode: all 6 sources with retry logic
"""

import re
import time
from bs4 import BeautifulSoup
from utils import get_session, clean_text, make_source


# ─────────────────────────────────────────────────────────────────────────────
# Search + fetch helpers
# ─────────────────────────────────────────────────────────────────────────────

def _search(query: str, max_results: int = 5) -> list:
    try:
        from ddgs import DDGS
        with DDGS() as d:
            return list(d.text(query, max_results=max_results))
    except Exception as e:
        print(f"  [search] {e}")
        return []

def _fetch(url: str, max_chars: int = 14000, verify_ssl: bool = True) -> str:
    try:
        resp = get_session().get(url, timeout=10, verify=verify_ssl)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script","style","nav","footer","header","aside","noscript"]):
            tag.decompose()
        return clean_text(soup.get_text(" ", strip=True), max_chars)
    except Exception as e:
        print(f"  [fetch] {url[:55]}: {e}")
        return ""

def _csr_relevant(text: str) -> bool:
    tl = text.lower()
    return sum(1 for t in ["csr","corporate social","sustainability","philanthrop",
                            "community","crore","education","skill","digital",
                            "social responsibility"] if t in tl) >= 2


_GENERIC_TOKENS = {"india","limited","ltd","private","pvt","the","and","of",
                   "company","corp","corporation","inc","group","technologies",
                   "solutions","services","international"}

def _mentions_company(company: str, text: str) -> bool:
    """
    Relevance guard: the fetched text must actually mention the company.
    Prevents generic portal pages / unrelated articles being marked FOUND.
    """
    if not text:
        return False
    tl = text.lower()
    tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", company.lower()).split()
              if len(t) > 2 and t not in _GENERIC_TOKENS]
    if not tokens:  # company name was all generic words — fall back to full name
        return company.lower() in tl
    return any(t in tl for t in tokens)


def _fetch_pdf(url: str, max_chars: int = 20000, max_pages: int = 40) -> str:
    """Download and extract text from a PDF (annual reports, CSR annexures)."""
    try:
        import io
        import pdfplumber
        resp = get_session().get(url, timeout=30)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            text = " ".join((p.extract_text() or "") for p in pdf.pages[:max_pages])
        return clean_text(text, max_chars)
    except Exception as e:
        print(f"  [pdf] {url[:55]}: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Source 1 — Company's India CSR / sustainability page
# ─────────────────────────────────────────────────────────────────────────────

def fetch_india_csr_page(company: str) -> dict:
    slug = re.sub(r"[^a-z0-9]", "", company.lower().split()[0])

    for url in [
        f"https://www.{slug}.com/in/en/about/csr",
        f"https://www.{slug}.com/india/csr",
        f"https://www.{slug}.com/sustainability",
        f"https://www.{slug}.com/corporate-social-responsibility",
        f"https://www.{slug}.com/en/about/sustainability",
    ]:
        text = _fetch(url)
        if text and len(text) > 600 and _csr_relevant(text) and _mentions_company(company, text):
            return make_source("india_csr_page", 1, url, text, "FOUND", "direct")

    for r in _search(f'"{company}" India CSR sustainability "corporate social"')[:5]:
        url  = r.get("href","")
        body = r.get("body","")
        if not url or any(s in url for s in ["youtube","twitter","linkedin","wikipedia"]):
            continue
        text = _fetch(url) or body
        if text and len(text) > 300 and _csr_relevant(text) and _mentions_company(company, text):
            return make_source("india_csr_page", 1, url, text, "FOUND", "search")

    return make_source("india_csr_page", 1, status="NOT_FOUND")


# ─────────────────────────────────────────────────────────────────────────────
# Source 2 — MCA portal  (deep search: CIN → CSR-2 filing)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_mca_portal(company: str) -> dict:
    """
    Strategy:
      1. Search for company's CIN (Corporate Identification Number) via web
      2. Try MCA master data API (public, no auth)
      3. Search for CSR-2 filing / board report snippets via DuckDuckGo
    The MCA portal itself is JS-heavy; we use public search + snippets as proxy.
    """
    # Step 1: try to find CIN
    cin = _find_cin(company)

    # Step 2: MCA master data (JS/CAPTCHA-gated — usually fails, but try)
    if cin:
        mca_url = f"https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do?cid={cin}"
        text = _fetch(mca_url)
        if text and len(text) > 200 and _mentions_company(company, text):
            src = make_source("mca_portal", 2, mca_url, text, "FOUND", "direct")
            src["cin"] = cin
            return src

    # Step 3: web search for MCA filing snippets.
    # HONESTY RULE: these are search-result proxies, NOT verified MCA filings.
    # They are labelled "mca_via_search" and get a lower source-quality score.
    for query in [
        f'"{company}" India "CSR-2" OR "Form CSR-2" OR "CSR committee" site:mca.gov.in',
        f'"{company}" India "CSR committee" "CSR obligation" crore annual report filing',
    ]:
        results = _search(query, max_results=4)
        for r in results:
            url  = r.get("href","")
            body = r.get("body","")
            text = _fetch(url) if url else ""
            if not text:
                text = body
            if text and _csr_relevant(text) and _mentions_company(company, text):
                src = make_source("mca_via_search", 2, url, text, "FOUND", "search_proxy")
                if cin:
                    src["cin"] = cin
                return src

    return make_source("mca_portal", 2, status="NOT_FOUND")


def _find_cin(company: str) -> str:
    """
    Try to find a company's CIN from public search results.
    CIN format: L/U + 5 digits + 2 letters + 4 digits + 3 letters + 6 digits
    e.g. L32202KA1994PLC016909
    """
    results = _search(f'"{company}" CIN "corporate identification number" India', max_results=3)
    cin_pattern = re.compile(
        r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b"
    )
    for r in results:
        body = r.get("body","") + r.get("title","")
        m = cin_pattern.search(body)
        if m:
            return m.group(0)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Source 3 — National CSR Portal (csr.gov.in)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_national_csr_portal(company: str) -> dict:
    company_q = company.replace(" ", "+")

    for url in [
        f"https://csr.gov.in/csr/companyprofile?company_name={company_q}",
        f"https://csr.gov.in/companySearch",
    ]:
        text = _fetch(url, verify_ssl=False)
        # CRITICAL: generic portal boilerplate must not count as FOUND —
        # the page must actually mention this company.
        if text and len(text) > 300 and _mentions_company(company, text):
            return make_source("national_csr_portal", 3, url, text, "FOUND", "direct")

    for r in (_search(f'site:csr.gov.in "{company}"') or
              _search(f'"{company}" "csr.gov.in" CSR portal India'))[:3]:
        url  = r.get("href","")
        body = r.get("body","")
        text = _fetch(url, verify_ssl=False) if url else ""
        if not text:
            text = body
        if text and _csr_relevant(text) and _mentions_company(company, text):
            return make_source("national_csr_portal", 3, url, text, "FOUND", "search")

    return make_source("national_csr_portal", 3, status="NOT_FOUND")


# ─────────────────────────────────────────────────────────────────────────────
# Source 4 — Annual / Sustainability Report
# ─────────────────────────────────────────────────────────────────────────────

def fetch_annual_report(company: str) -> dict:
    """
    Searches for the most recent annual report or sustainability report.
    v5: parses PDFs directly with pdfplumber (Indian annual reports and
    CSR annexures are almost always PDFs).
    """
    for r in _search(
        f"{company} annual report 2026 2025 sustainability CSR India", max_results=6
    ):
        url  = r.get("href","")
        body = r.get("body","")
        if not url:
            continue
        if url.lower().endswith(".pdf"):
            text = _fetch_pdf(url)
            if text and len(text) > 500 and _mentions_company(company, text):
                return make_source("annual_report", 4, url, text, "FOUND", "pdf")
            # fall back to the search snippet only if PDF parsing failed
            if body and len(body) > 100 and _mentions_company(company, body):
                return make_source("annual_report", 4, url, body, "FOUND", "snippet")
            continue
        text = _fetch(url) or body
        if text and len(text) > 300 and _mentions_company(company, text):
            return make_source("annual_report", 4, url, text, "FOUND", "search")

    return make_source("annual_report", 4, status="NOT_FOUND")


# ─────────────────────────────────────────────────────────────────────────────
# Source 5 — Funded / implementation partners (NEW)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_partner_source(company: str) -> dict:
    """
    Targeted search for the NGOs / implementation partners a company funds
    through CSR. Fetches the best page mentioning both the company and
    partner language.
    """
    for query in [
        f'"{company}" CSR "implementation partner" OR "implementing partner" OR "NGO partner" India',
        f'"{company}" CSR "partnered with" foundation OR trust OR NGO India 2026 2025',
    ]:
        for r in _search(query, max_results=5):
            url  = r.get("href","")
            body = r.get("body","")
            if not url or any(s in url for s in ["youtube","twitter","facebook"]):
                continue
            text = (_fetch_pdf(url) if url.lower().endswith(".pdf") else _fetch(url)) or body
            if text and len(text) > 300 and _csr_relevant(text) and _mentions_company(company, text):
                return make_source("partner_search", 5, url, text, "FOUND", "search")

    return make_source("partner_search", 5, status="NOT_FOUND")


# ─────────────────────────────────────────────────────────────────────────────
# Source 6 — CSR decision-makers / LinkedIn (NEW)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_people_sources(company: str) -> dict:
    """
    Finds CSR decision-makers via public search, prioritising LinkedIn results.
    LinkedIn blocks direct fetches, so we use the search-result TITLES and
    SNIPPETS (which LinkedIn itself publishes) as evidence — never fabricated.
    Returns one source whose text is the concatenated result snippets, plus a
    'people_hits' list of {title, snippet, url} preserved for the parser.
    """
    hits = []
    queries = [
        f'site:linkedin.com/in "{company}" "CSR" OR "corporate social responsibility" India',
        f'site:linkedin.com/in "{company}" "head" sustainability OR CSR',
        f'"{company}" "head of CSR" OR "CSR head" OR "chief sustainability officer" India name',
    ]
    for q in queries:
        for r in _search(q, max_results=5):
            url   = r.get("href","")
            title = r.get("title","")
            body  = r.get("body","")
            if not url:
                continue
            blob = f"{title} {body}"
            if _mentions_company(company, blob):
                hits.append({"title": title, "snippet": body, "url": url})
        if len(hits) >= 6:
            break

    if not hits:
        return make_source("people_search", 6, status="NOT_FOUND")

    text = " || ".join(f"{h['title']} — {h['snippet']}" for h in hits)
    src  = make_source("people_search", 6, hits[0]["url"], clean_text(text, 8000),
                       "FOUND", "search_snippets")
    src["people_hits"] = hits[:10]
    return src


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrators — screen mode (fast) vs deep mode (thorough)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_screen_sources(company: str) -> list:
    """
    Screen mode: Sources 1 + 4 only. Target: < 45 seconds.
    If source 1 finds rich data, skip source 4.
    """
    print(f"\n[SCREEN] {company}")
    s1 = fetch_india_csr_page(company)
    print(f"  Source 1 (CSR page):    {s1['status']}")
    time.sleep(0.2)

    s4 = fetch_annual_report(company)
    print(f"  Source 4 (Annual rpt):  {s4['status']}")

    # Pad to 4 for state-determination compatibility
    s2 = make_source("mca_portal",           2, status="NOT_TRIED")
    s3 = make_source("national_csr_portal",  3, status="NOT_TRIED")
    s5 = make_source("partner_search",       5, status="NOT_TRIED")
    s6 = make_source("people_search",        6, status="NOT_TRIED")
    return [s1, s2, s3, s4, s5, s6]


def fetch_deep_sources(company: str, progress_cb=None) -> list:
    """
    Deep mode: All 6 sources, with MCA CIN lookup.
    """
    def _step(msg):
        """Major step — advances the UI progress bar."""
        print(f"  {msg}")
        if progress_cb:
            progress_cb(msg)

    def _note(msg):
        """Sub-status — console only, does NOT advance the progress bar."""
        print(f"  {msg}")

    print(f"\n[DEEP RESEARCH] {company}")

    _step("Source 1/6 — India CSR page...")
    s1 = fetch_india_csr_page(company)
    _note(f"  → {s1['status']}  {s1['url'][:60]}")
    time.sleep(0.3)

    _step("Source 2/6 — MCA portal + CIN lookup...")
    s2 = fetch_mca_portal(company)
    _note(f"  → {s2['status']}")
    time.sleep(0.3)

    _step("Source 3/6 — National CSR Portal...")
    s3 = fetch_national_csr_portal(company)
    _note(f"  → {s3['status']}")
    time.sleep(0.3)

    _step("Source 4/6 — Annual report...")
    s4 = fetch_annual_report(company)
    _note(f"  → {s4['status']}")
    time.sleep(0.3)

    _step("Source 5/6 — Funded partners...")
    s5 = fetch_partner_source(company)
    _note(f"  → {s5['status']}")
    time.sleep(0.3)

    _step("Source 6/6 — CSR decision-makers (LinkedIn)...")
    s6 = fetch_people_sources(company)
    _note(f"  → {s6['status']}")

    found = sum(1 for s in [s1,s2,s3,s4,s5,s6] if s["status"] == "FOUND")
    print(f"\n  {found}/6 sources returned data.")
    return [s1, s2, s3, s4, s5, s6]
