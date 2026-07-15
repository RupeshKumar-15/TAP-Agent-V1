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
