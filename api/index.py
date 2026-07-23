# api/index.py — Vercel serverless entrypoint (Flask)
# All routes are rewritten here by vercel.json. The research pipeline modules
# live in the repo root, one directory up.
import base64
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request  # noqa: E402

# Import the pipeline defensively: if anything fails to import on Vercel,
# serve the real traceback instead of an opaque FUNCTION_INVOCATION_FAILED.
_IMPORT_ERROR = None
try:
    from scraper import fetch_screen_sources, fetch_deep_sources
    from parser import parse_all
    from scorer import score as compute_score, _cfg as load_cfg
    from methodology import derive_criteria
    from reporter import generate_html_report
    from docx_reporter import generate_docx_report
    from deep_dive_xlsx import generate_deep_dive_xlsx
    from webui import render_home, render_results
except Exception:
    _IMPORT_ERROR = traceback.format_exc()

app = Flask(__name__)


@app.errorhandler(500)
def _err(e):
    return "<pre>Internal error — check Vercel runtime logs.</pre>", 500


def _import_error_page():
    return (f"<h3>Import failed on the server</h3>"
            f"<pre style='white-space:pre-wrap'>{_IMPORT_ERROR}</pre>", 500)

_DOCX_MIME = ("application/vnd.openxmlformats-officedocument."
              "wordprocessingml.document")
_XLSX_MIME = ("application/vnd.openxmlformats-officedocument."
              "spreadsheetml.sheet")


@app.get("/")
def home():
    if _IMPORT_ERROR:
        return _import_error_page()
    return render_home()


@app.post("/research")
def research():
    if _IMPORT_ERROR:
        return _import_error_page()
    company = (request.form.get("company") or "").strip()
    mode    = request.form.get("mode", "screen")
    if mode not in ("screen", "deep"):
        mode = "screen"
    if not company:
        return render_home(error="Please enter a company name.")

    # ── Pipeline: fetch → parse → score → methodology ────────────────────────
    sources = (fetch_screen_sources(company) if mode == "screen"
               else fetch_deep_sources(company))
    parsed  = parse_all(sources, company)
    result  = compute_score(company, sources, parsed)
    cfg     = load_cfg()
    meth    = derive_criteria(company, result, cfg)

    # Claude full-company analysis (deep mode only; degrades silently to None
    # without ANTHROPIC_API_KEY). Its fit score is a diagnostic — the verdict
    # of record remains the methodology scorecard.
    analysis = None
    if mode == "deep":
        try:
            from llm_analysis import analyze_company
            from textproc import build_sources_manifest
            analysis = analyze_company(
                company,
                cfg.get("org_mission", ""),
                sources,
                build_sources_manifest(sources),
            )
        except Exception:
            import logging
            logging.getLogger("tap.api").exception("analysis failed — continuing without it")
            analysis = None
    result["analysis"] = analysis

    # ── Decision-makers: keep only CURRENT CSR role-holders ─────────────────
    # The keyword extractor returns anyone whose LinkedIn snippet mentions the
    # company — including people who LEFT years ago and 'People also viewed'
    # sidebar names. The LLM matcher reads tenure language and drops former/ex
    # roles, keeping only current subjects. Without a key we cannot verify
    # tenure, so the keyword list is kept but flagged UNVERIFIED.
    if mode == "deep":
        people_src = next((s for s in sources
                           if s.get("source_name") == "people_search"), None)
        hits = (people_src or {}).get("people_hits", []) if people_src else []
        refined = []
        try:
            from llm_analysis import match_people_from_search, analysis_enabled
            if hits:
                refined = match_people_from_search(company, hits)
        except Exception:
            import logging
            logging.getLogger("tap.api").exception("people match failed")
            refined = []

        data = result.setdefault("data", {})
        if refined:
            data["decision_makers"] = [{
                "name": p["name"], "title": p["title"],
                "linkedin_url": p.get("linkedin_url", ""),
                "tenure_status": p.get("tenure_status", "UNKNOWN"),
                "current_role": True,
                "source_url": p.get("linkedin_url", ""),
                "excerpt": p.get("reasoning", ""),
            } for p in refined]
            data["decision_makers_verified"] = True
        else:
            # No LLM verification available — mark existing rows unverified.
            for d in data.get("decision_makers", []) or []:
                d.setdefault("current_role", None)
            data["decision_makers_verified"] = False

    # ── Merged scoring (fork's model): when the Claude analysis is available
    # its holistic fit_score IS the fit score; the deterministic engine score
    # is kept as the fallback (used whenever the LLM is unavailable) and
    # remains visible as a diagnostic. One verdict, one source of truth.
    if analysis and analysis.get("fit_score"):
        from scorer import get_scoring_tier, score_band
        result["engine_fit_score"] = result["fit_score"]
        result["fit_score"]        = int(analysis["fit_score"])
        result["scoring_tier"]     = get_scoring_tier(result["fit_score"])
        result["band"]             = score_band(result["fit_score"], cfg)
        result["score_source"]     = "claude_analysis"
        rationale = (analysis.get("fit_rationale") or "").replace("**", "")
        if rationale:
            result["strategic_insight"] = f"{rationale} {result.get('strategic_insight','')}"
    else:
        result["engine_fit_score"] = result["fit_score"]
        result["score_source"]     = "deterministic_engine"

    # ── Deep mode: generate all report files now and embed them in the page
    #    (serverless functions share no memory between requests, so there is
    #    no session to fetch them from later) ──────────────────────────────────
    files = {}
    if mode == "deep":
        safe = company.replace(" ", "_")

        docx_bytes = generate_docx_report(company, result, mode="deep")
        files["📝 DOCX brief (leadership)"] = (
            f"TAP_CSR_Brief_{safe}.docx", _DOCX_MIME,
            base64.b64encode(docx_bytes).decode())

        html_report = generate_html_report(company, result, mode="deep")
        files["📄 HTML report"] = (
            f"tap_csr_{safe.lower()}_deep.html", "text/html",
            base64.b64encode(html_report.encode("utf-8")).decode())

        xlsx_bytes = generate_deep_dive_xlsx(company, result, cfg)
        files["📊 Deep-dive base (XLSX, 7 sheets)"] = (
            f"TAP_DeepDive_{safe}.xlsx", _XLSX_MIME,
            base64.b64encode(xlsx_bytes).decode())

        export = dict(result)
        # THE single verdict, mirrored at the top level of the JSON
        _t = result.get("scoring_tier", {}) or {}
        export["verdict"] = {
            "fit_score": result.get("fit_score", 0),
            "tier": _t.get("label", ""), "action": _t.get("action", ""),
            "source": result.get("score_source", "deterministic_engine"),
            "engine_fit_score": result.get("engine_fit_score",
                                            result.get("fit_score", 0)),
            "methodology_average": meth["average"],
            "methodology_tier": meth["tier"]["label"],
        }
        export["sources"] = [
            {k: v for k, v in s.items() if k not in ("text", "people_hits")}
            for s in result.get("sources", [])]
        export["methodology"] = meth
        files["⬇️ JSON export"] = (
            f"tap_csr_{safe.lower()}.json", "application/json",
            base64.b64encode(json.dumps(
                export, indent=2, ensure_ascii=False, default=str
            ).encode("utf-8")).decode())

    return render_results(company, mode, result, meth, files)


# Local development: `python api/index.py` then open http://localhost:5000
if __name__ == "__main__":
    app.run(debug=True)
