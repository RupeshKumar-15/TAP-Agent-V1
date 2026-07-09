# TAP CSR Research Agent — Sharing & Leadership Pitch Guide

## 1. How to share the tool with senior leadership

The key insight: leadership doesn't need to *run* the tool — they need its *outputs*. Two layers:

**Layer 1 — Share the reports (works today, zero setup)**
The fundraising team runs the tool and downloads the **DOCX brief** for each prospect. It is designed for leadership: executive summary up front, score visual, funded partners with TAP-similarity flags, decision-makers with LinkedIn links, and a verification log proving nothing was made up. Email it, print it, or drop it in a shared drive. The HTML report is a self-contained backup that opens in any browser and supports dark mode.

**Layer 2 — Share the live tool (when leadership wants self-serve)**
Options in order of ease:

1. **Streamlit Community Cloud (recommended, free).** Push this folder to a private GitHub repo → share.streamlit.io → deploy `app.py` → share the URL. Restrict viewers to specific email addresses in app settings. Leadership gets a link that works from any device; you push updates by pushing to GitHub.
2. **Hugging Face Spaces (free).** Create a private Streamlit Space, upload these files. Similar result.
3. **Office network.** Run `streamlit run app.py --server.address 0.0.0.0` on one machine; colleagues on the same Wi-Fi open `http://<your-ip>:8501`. No internet exposure, no accounts.

Avoid sending the code folder itself to leadership — they would need Python installed. Send reports or a URL.

## 2. Pitch pointers — Fundraising Head & CEO

**Opening frame (one sentence):** "This is a pre-sales engine: it compresses 2–4 hours of manual prospect research into ~2 minutes, without ever inventing a fact."

**For the Fundraising Head (cares about pipeline & quality):**
- Two-speed workflow: *Screening* triages a long company list into QUALIFY / WATCHLIST / SKIP in under a minute each; *Deep Research* produces the outreach-ready brief only for qualified prospects.
- The new intelligence layer answers the two questions that matter before outreach: **who do they already fund** (a company funding a TAP-peer NGO like Pratham or Quest Alliance has already proven willingness to fund our model) and **who signs the cheque** (CSR decision-makers with LinkedIn links).
- Every brief ends with a partnership angle (adjacency reasoning) — the first line of the pitch email writes itself.
- Tunable without a developer: scoring weights, focus keywords, peer-NGO list all live in one config file.

**For the CEO (cares about trust & risk):**
- Zero-hallucination architecture: a fact is only published if it carries a verbatim excerpt from a fetched source; every report includes a verification log where each fact is re-checked against raw source text. Unknown data is labelled "not publicly disclosed" — never estimated.
- Proxy data is labelled as proxy (e.g., MCA info via web search is marked "not a verified filing" and scored lower).
- Data comes only from prelisted public sources: company CSR pages, MCA, National CSR Portal (csr.gov.in), annual reports, and LinkedIn's own published snippets.
- Human-in-the-loop: decision-maker names must be verified before outreach — the tool says so on the report itself.

**Demo script (5 minutes):**
1. Screen a known good prospect live (e.g., a company you already know funds education) — show the QUALIFY verdict.
2. Run Deep Research on it — walk through funded partners (point at a TAP-peer badge), decision-makers, and the verification log.
3. Download the DOCX and open it: "this is what lands in your inbox."
4. Close with the ask: adopt as the standard pre-outreach step; fundraisers feed learnings back into the config.

**Numbers to anchor on:** hours saved per prospect (2–4 → ~0.05), prospects screenable per week (50+), and 100% of published facts carrying a source citation.

**Anticipate these questions:**
- *"What if data is wrong?"* → Every fact links to its source; the verification log flags anything that fails re-checking as CHECK MANUALLY.
- *"What about companies with no public CSR data?"* → The tool says so explicitly (CONFIRMED_ABSENT) and recommends direct outreach — it never fills gaps with guesses.
- *"Is LinkedIn scraping a risk?"* → We never scrape LinkedIn pages; we only use the titles/snippets LinkedIn itself publishes to search engines, plus clearly-labelled search links.
