# llm_analysis.py — Claude full-company analysis (sync port of the fork's llm.py)
"""
One comprehensive Claude call produces a structured analysis of the company:
narrative rationale, spend + trend, programmes, partners, decision-makers with
tenure, red flags, contact pathway, RFP/board/volunteering/group-foundation
signals, Section 135 eligibility, sector, 17 scored criteria, open questions.

Production hardening carried over from the fork:
  - Pydantic validation with clamped ranges on every field
  - Rolling token-per-minute budgeter + cooldown on 429 (retry-after honoured)
  - 2s pacing between calls; prompt auto-shrink to fit the TPM budget
  - Assistant-prefill "{" forces JSON; front-loaded keys + partial-JSON
    recovery survive truncation
  - Consistency backfill: facts named in prose are injected into their
    structured fields

FULL FALLBACK: without ANTHROPIC_API_KEY every entry point returns None and
the deterministic engine + methodology scorecard stand alone.

NOTE ON THE VERDICT: this module's fit_score is an AI-analyst DIAGNOSTIC.
The single verdict of record remains the methodology scorecard.
"""
import json
import logging
import os
import re
import time

import requests
import yaml

try:
    from pydantic import BaseModel, Field, ValidationError
    _PYDANTIC = True
except ImportError:  # degrade: module unusable but importable
    _PYDANTIC = False

from textproc import (build_token_budgeted_evidence, estimate_tokens,
                      structure_all_sources)

logger = logging.getLogger("tap.llm_analysis")

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

OUTPUT_TOKEN_RESERVE = 5200
SCAFFOLD_SAFETY_MARGIN = 250
MIN_EVIDENCE_BUDGET = 350
INTER_CALL_DELAY_SECONDS = 2.0


def _cfg() -> dict:
    p = os.path.join(os.path.dirname(__file__), "config.yaml")
    try:
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return {}


def _analysis_cfg() -> dict:
    return _cfg().get("anthropic_analysis", {}) or {}


def _api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def _model() -> str:
    return (os.getenv("ANTHROPIC_MODEL", "").strip()
            or _analysis_cfg().get("model", "claude-sonnet-4-5"))


def _tpm_limit() -> int:
    try:
        return int(os.getenv("ANTHROPIC_TPM_LIMIT", "")
                   or _analysis_cfg().get("tpm_limit", 30000))
    except (TypeError, ValueError):
        return 30000


def analysis_enabled() -> bool:
    return bool(_PYDANTIC and _api_key()
                and _analysis_cfg().get("enabled", True))


# ── 17 criteria ──────────────────────────────────────────────────────────────
CRITERIA_IDS = [
    "education_intervention", "stem", "tech_21cs", "public_schooling",
    "systems_change", "programme_depth", "partnership_quality",
    "decision_maker_accessibility", "csr_trajectory", "delivery_model_fit",
    "outreach_readiness", "funding_capacity", "csr_spend_trend",
    "decision_maker_tenure", "group_foundation_routing",
    "board_education_affinity", "employee_volunteering",
]

CRITERIA_TITLES = {
    "education_intervention": "Education: intervention not scholarship",
    "stem": "STEM exposure",
    "tech_21cs": "Technology & 21st-century skills",
    "public_schooling": "Public-schooling understanding",
    "systems_change": "Systems-change orientation",
    "programme_depth": "Programme maturity & depth",
    "partnership_quality": "NGO partnership quality",
    "decision_maker_accessibility": "Decision-maker accessibility",
    "csr_trajectory": "CSR trajectory (growing / flat / shrinking)",
    "delivery_model_fit": "Delivery-model fit for TAP entry",
    "outreach_readiness": "Outreach readiness (open call / RFP / warm channel)",
    "funding_capacity": "Funding capacity vs TAP's typical ask size",
    "csr_spend_trend": "Multi-year CSR spend trend",
    "decision_maker_tenure": "CSR-head tenure (newly appointed vs entrenched)",
    "group_foundation_routing": "CSR routed through a group/parent foundation",
    "board_education_affinity": "Board or promoter personal education-philanthropy ties",
    "employee_volunteering": "Employee volunteering / payroll-giving programmes",
}

_RUBRIC = {
    "education_intervention": "hands-on programme not scholarship",
    "stem": "named STEM/coding/robotics/science exposure",
    "tech_21cs": "tech-delivered learning or 21st-c-skills",
    "public_schooling": "explicit government-school work",
    "systems_change": "teacher training, outcomes, scale/policy",
    "programme_depth": "one-off=lower, named multi-year=higher",
    "partnership_quality": "unnamed single-year=lower, named multi-year=higher",
    "decision_maker_accessibility": "named individual with current CSR-decision title",
    "csr_trajectory": "expansion=higher, static=medium, contraction=lower, no signal=0",
    "delivery_model_fit": "how cleanly TAP could enter as grantee or delivery partner",
    "outreach_readiness": "open call/RFP=high, closed programme=low",
    "funding_capacity": "does the company's disclosed CSR budget look able to plausibly fund a grant of TAP's typical size",
    "csr_spend_trend": "rising multi-year=high, flat=medium, declining=low, no data=0",
    "decision_maker_tenure": "recently appointed=higher signal of new priorities, long entrenched/no signal=lower",
    "group_foundation_routing": "named parent foundation=high, no signal=0",
    "board_education_affinity": "named personal history=higher, generic=low, none=0",
    "employee_volunteering": "active named education programme=higher, generic=low, none=0",
}


# ── Schemas ──────────────────────────────────────────────────────────────────
if _PYDANTIC:
    class CriterionResultSchema(BaseModel):
        id: str
        score: float = Field(ge=0, le=5)
        confidence: int = Field(ge=0, le=100)
        evidence: str = Field(default="", max_length=240)
        reasoning: str = Field(default="", max_length=240)
        source: str = Field(default="")

    class SpendYearSchema(BaseModel):
        fiscal_year: str = ""
        inr_crore: float | None = None
        display: str = ""
        source: str = ""
        source_excerpt: str = Field(default="", max_length=200)

    class SpendSchema(BaseModel):
        inr_crore: float | None = None
        display: str = ""
        fiscal_year: str = ""
        has_disclosed_budget: bool = False
        confidence: int = Field(ge=0, le=100, default=0)
        source_excerpt: str = Field(default="", max_length=200)
        source: str = ""
        trend_direction: str = "UNKNOWN"
        trend_evidence: str = Field(default="", max_length=240)
        trend_source: str = ""
        history: list[SpendYearSchema] = Field(default_factory=list)

    class ProgrammeSchema(BaseModel):
        name: str = ""
        description: str = Field(default="", max_length=220)
        is_multi_year: bool = False
        cohort_or_scale: str = ""
        source_excerpt: str = Field(default="", max_length=200)
        source: str = ""

    class PartnerSchema(BaseModel):
        name: str = ""
        relationship_type: str = ""
        source_excerpt: str = Field(default="", max_length=200)
        source: str = ""

    class DecisionMakerSchema(BaseModel):
        name: str = ""
        title: str = ""
        public_facing_score: int = Field(ge=0, le=100, default=0)
        tenure_status: str = "UNKNOWN"
        tenure_evidence: str = Field(default="", max_length=200)
        source_excerpt: str = Field(default="", max_length=200)
        source: str = ""

    class GeographySchema(BaseModel):
        place: str = ""
        source_excerpt: str = Field(default="", max_length=160)
        source: str = ""

    class RedFlagSchema(BaseModel):
        flag: str = ""
        severity: str = ""
        explanation: str = Field(default="", max_length=220)
        source: str = ""

    class ContactPathwaySchema(BaseModel):
        channel: str = ""
        evidence: str = Field(default="", max_length=200)
        source: str = ""

    class RfpSignalSchema(BaseModel):
        present: bool = False
        channel: str = ""
        evidence: str = Field(default="", max_length=220)
        source: str = ""

    class BoardAffinitySchema(BaseModel):
        present: bool = False
        person_name: str = ""
        connection: str = Field(default="", max_length=220)
        source_excerpt: str = Field(default="", max_length=200)
        source: str = ""

    class VolunteeringSchema(BaseModel):
        present: bool = False
        programme_name: str = ""
        description: str = Field(default="", max_length=220)
        source_excerpt: str = Field(default="", max_length=200)
        source: str = ""

    class GroupFoundationSchema(BaseModel):
        routed_through_group: bool = False
        foundation_name: str = ""
        explanation: str = Field(default="", max_length=240)
        source_excerpt: str = Field(default="", max_length=200)
        source: str = ""

    class EligibilitySchema(BaseModel):
        plausibly_mandated: str = "UNKNOWN"
        reasoning: str = Field(default="", max_length=280)
        net_worth_turnover_signal: str = Field(default="", max_length=200)
        source: str = ""

    class SectorSchema(BaseModel):
        sector: str = "UNKNOWN"
        sub_sector: str = ""
        reasoning: str = Field(default="", max_length=200)

    class FullAnalysisSchema(BaseModel):
        fit_score: int = Field(ge=0, le=100, default=0)
        fit_rationale: str = Field(default="", max_length=600)
        overall_semantic_alignment: int = Field(ge=0, le=100, default=0)
        alignment_rationale: str = Field(default="", max_length=500)
        delivery_model: str = "UNCLEAR"
        delivery_model_evidence: str = Field(default="", max_length=220)
        delivery_model_source: str = ""
        spend: SpendSchema = SpendSchema()
        programmes: list[ProgrammeSchema] = Field(default_factory=list)
        partners: list[PartnerSchema] = Field(default_factory=list)
        decision_makers: list[DecisionMakerSchema] = Field(default_factory=list)
        geographies: list[GeographySchema] = Field(default_factory=list)
        criteria: list[CriterionResultSchema] = Field(default_factory=list)
        red_flags: list[RedFlagSchema] = Field(default_factory=list)
        contact_pathway: ContactPathwaySchema = ContactPathwaySchema()
        rfp_signal: RfpSignalSchema = RfpSignalSchema()
        board_affinity: BoardAffinitySchema = BoardAffinitySchema()
        volunteering: VolunteeringSchema = VolunteeringSchema()
        group_foundation: GroupFoundationSchema = GroupFoundationSchema()
        eligibility: EligibilitySchema = EligibilitySchema()
        sector: SectorSchema = SectorSchema()
        evidence_recency: str = Field(default="", max_length=160)
        csr_head_note: str = Field(default="", max_length=320)
        source_quality_assessment: str = Field(default="", max_length=320)
        overall_authenticity_score: int = Field(ge=0, le=100, default=0)
        open_questions: list[str] = Field(default_factory=list)


def clamp_int(value, minimum: int, maximum: int, default: int) -> int:
    if not isinstance(value, (int, float)):
        return default
    return int(min(max(value, minimum), maximum))


def clamp_float(value, minimum: float, maximum: float, default: float) -> float:
    if not isinstance(value, (int, float)):
        return default
    return round(min(max(float(value), minimum), maximum), 1)


# ── TPM budget + cooldown + pacing ───────────────────────────────────────────
_cooldown_until = 0.0
_cooldown_reason = ""
_TPM_WINDOW_SECONDS = 60.0
_tpm_events: list = []
_last_call_finished = 0.0


def _prune_tpm(now: float) -> None:
    cutoff = now - _TPM_WINDOW_SECONDS
    while _tpm_events and _tpm_events[0][0] < cutoff:
        _tpm_events.pop(0)


def _record_tpm(tokens: int) -> None:
    now = time.monotonic()
    _prune_tpm(now)
    _tpm_events.append((now, tokens))


def tpm_used() -> int:
    _prune_tpm(time.monotonic())
    return sum(t for _, t in _tpm_events)


def _parse_retry_after(header: str, body: str) -> float:
    try:
        return float(header)
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in ([\d.]+)s", body)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 30.0


def cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - time.monotonic())


def call_anthropic_chat(prompt: str, max_tokens: int = 1400,
                        temperature: float = 0.0, caller: str = "unknown"):
    """Single synchronous Claude call. Returns text (prefixed '{') or None."""
    global _cooldown_until, _cooldown_reason, _last_call_finished

    if not _api_key():
        logger.warning("anthropic skipped caller=%s reason=not_configured", caller)
        return None
    if cooldown_remaining() > 0:
        logger.warning("anthropic skipped caller=%s cooldown=%.0fs reason=%s",
                       caller, cooldown_remaining(), _cooldown_reason)
        return None

    since_last = time.monotonic() - _last_call_finished
    if since_last < INTER_CALL_DELAY_SECONDS:
        time.sleep(INTER_CALL_DELAY_SECONDS - since_last)

    tpm_limit = _tpm_limit()
    est_prompt = estimate_tokens(prompt)
    est_total = est_prompt + max_tokens
    if est_prompt > tpm_limit - max_tokens:
        logger.error("anthropic aborted caller=%s prompt too big (%d tokens)",
                     caller, est_prompt)
        return None
    used = tpm_used()
    if used + est_total > tpm_limit:
        _cooldown_until = max(_cooldown_until, time.monotonic() + 15.0)
        _cooldown_reason = "local tpm budget exhausted"
        logger.warning("anthropic skipped caller=%s reason=tpm_budget used=%d est=%d limit=%d",
                       caller, used, est_total, tpm_limit)
        return None

    payload = {
        "model": _model(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "{"},   # prefill forces JSON
        ],
    }
    _record_tpm(est_total)
    started = time.monotonic()
    try:
        resp = requests.post(
            ANTHROPIC_MESSAGES_URL,
            headers={"x-api-key": _api_key(),
                     "anthropic-version": ANTHROPIC_API_VERSION,
                     "Content-Type": "application/json"},
            json=payload, timeout=90,
        )
    except requests.RequestException as exc:
        logger.error("anthropic transport error caller=%s err=%s", caller, exc)
        _last_call_finished = time.monotonic()
        return None
    _last_call_finished = time.monotonic()

    if resp.status_code == 429:
        wait = _parse_retry_after(resp.headers.get("retry-after", ""), resp.text)
        _cooldown_until = time.monotonic() + wait
        _cooldown_reason = resp.text[:200]
        logger.warning("anthropic 429 caller=%s retry_after=%.0fs", caller, wait)
        return None
    if resp.status_code >= 400:
        logger.error("anthropic http %d caller=%s body=%s",
                     resp.status_code, caller, resp.text[:300])
        return None
    try:
        body = resp.json()
    except ValueError:
        return None

    usage = body.get("usage") or {}
    if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
        _record_tpm(usage["input_tokens"] + usage["output_tokens"] - est_total)

    if body.get("stop_reason") == "max_tokens":
        logger.warning("anthropic TRUNCATED caller=%s — attempting partial-JSON recovery",
                       caller)

    parts = [b.get("text", "") for b in (body.get("content") or [])
             if b.get("type") == "text"]
    if not parts:
        return None
    logger.info("anthropic ok caller=%s elapsed=%.0fms",
                caller, (time.monotonic() - started) * 1000)
    return "{" + "".join(parts)


# ── Prompt ───────────────────────────────────────────────────────────────────
def _criteria_rubric_block() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in _RUBRIC.items())


def _criteria_json_template() -> str:
    return ",\n".join(
        '    {{"id": "{id}", "score": <0-5>, "confidence": <0-100>, '
        '"evidence": "<short>", "reasoning": "<short>"}}'.format(id=cid)
        for cid in CRITERIA_IDS)


HIGHLIGHT_RULE = (
    "MARKER-HIGHLIGHT RULE (applies to fit_rationale, alignment_rationale, "
    "delivery_model_evidence, source_quality_assessment, csr_head_note, "
    "evidence_recency, contact_pathway.channel, and every criterion's evidence "
    "field): inside that field's text, wrap the single most decision-relevant "
    "phrase in **double asterisks**. The phrase must be exactly 2 to 3 words. "
    "Never bold more than one phrase per field, and never bold anything in "
    "name fields, titles, labels, source names, urls, booleans, or enums."
)

OUTPUT_ORDER_RULE = (
    "OUTPUT ORDER RULE: write the JSON object with fit_score, fit_rationale, "
    "overall_semantic_alignment, alignment_rationale, delivery_model, and "
    "delivery_model_evidence as the first six keys, in that order, before any "
    "other field, so the most important fields survive if output runs low."
)


def full_company_analysis_prompt(company: str, mission: str,
                                 evidence_text: str, sources_manifest: str) -> str:
    return f"""You are a thoughtful, fair-minded CSR partnerships analyst judging whether {company} is a good funding/partnership fit for an Indian education NGO. Read the evidence and form a holistic judgment, giving reasonable benefit of the doubt where evidence is plausibly consistent with a good fit.

NGO MISSION: {mission}

SOURCES FETCHED (name | status | url):
{sources_manifest}

EVIDENCE:
\"\"\"
{evidence_text}
\"\"\"

{OUTPUT_ORDER_RULE}

HARD RULE ON COMPLETENESS: every output field below is mandatory. A blank narrative field (fit_rationale, alignment_rationale, delivery_model_evidence, source_quality_assessment, csr_head_note, evidence_recency) is only acceptable if the evidence truly contains nothing usable for it.

CRITICAL CONSISTENCY RULE: any fact stated in prose MUST also appear in its structured field: (a) named programmes -> programmes array; (b) named NGOs/foundations -> partners array; (c) any revenue/turnover/CSR figure -> spend.has_disclosed_budget true + spend.display (label inferred minimums); (d) a recommended entry-point person -> contact_pathway.channel AND decision_makers; (e) any state/city -> geographies. Before finalising, re-read your rationale fields and add anything you missed to the structured arrays.

{HIGHLIGHT_RULE}

1. FIT SCORE 0-100: holistic judgment — do NOT compute mechanically from criteria. Sparse public sourcing is common and is not evidence of poor fit. A large technology / IT-services / professional-services company has meaningful sector proximity to an ed-tech NGO — treat that as a positive. Plausible sector fit with limited documentation -> 60-75; a named concrete programme with real mission alignment -> 70-85. Reserve <40 for evidence of actively poor fit (wrong sector, zero CSR, conflict with mission).
2. FIT RATIONALE (2-4 sentences, required): explain the score from specific evidence; lead with what is promising.
3. SEMANTIC ALIGNMENT 0-100 + ALIGNMENT RATIONALE (required): overlap of actual/plausible CSR activity with the mission. Tech/education-adjacent sector scores at least 50-60 on plausibility alone.
4. DELIVERY MODEL: FUNDER/IMPLEMENTER/HYBRID/UNCLEAR + evidence sentence naming the supporting programme/statement.
5. BUDGET: any stated or inferable India CSR figure (incl. revenue x 2% mandate) -> has_disclosed_budget true + display (label inferred); prior years -> history[]; trend from actual numbers only. Undisclosed budget = open question, not a negative.
6. PROGRAMMES: every named programme/initiative/campaign, multi-year vs one-off, scale.
7. PARTNERS: every NGO/foundation/organisation worked with, relationship_type funder/implementer/co-design/unclear.
8. DECISION MAKERS: every named leader in CSR/sustainability context: title, public_facing_score 0-100, tenure_status.
9. GEOGRAPHY: every state/city mentioned.
10. RFP SIGNAL: explicit call for NGO partners.
11. BOARD AFFINITY: named board/promoter education-philanthropy history.
12. VOLUNTEERING: named employee volunteering/payroll-giving touching education.
13. GROUP FOUNDATION: CSR run via separate parent/group foundation.
14. ELIGIBILITY: Section 135 applicability LIKELY/UNLIKELY/UNKNOWN from net worth/turnover/profit signals.
15. SECTOR: classify from any company-description language, sub_sector if clear.
16. CRITERIA 0-5 each, all ids in order, short evidence+reasoning, supporting detail only:
{_criteria_rubric_block()}
17. RED FLAGS: genuine contradictions or marketing-not-substance signals only. Unconfirmed details belong in open_questions, not red_flags.
18. CONTACT PATHWAY (required): the single most concrete real channel found.
19. EVIDENCE RECENCY (required sentence): how recent the evidence appears.
20. CSR HEAD NOTE (required sentence): leadership philosophy, or how CSR appears organised.
21. SOURCE QUALITY ASSESSMENT (required 1-2 sentences): honest strength of the sources actually used.
22. AUTHENTICITY SCORE 0-100: trust in the sourcing.
23. OPEN QUESTIONS: up to 5 short items to verify.

Missing evidence for a criterion: score 0, confidence 0, evidence "To confirm — no signal in evidence".

Rules: evidence fields are paraphrases under 20 words, never verbatim. Never fabricate. In every "source" field use ONLY a source name from the SOURCES FETCHED list. Keep strings concise so the reply fits {OUTPUT_TOKEN_RESERVE} output tokens; prioritise the first six keys. Reply with ONE JSON object, nothing else.

JSON shape:
{{
  "fit_score": <int>, "fit_rationale": "<2-4 sentences>",
  "overall_semantic_alignment": <int>, "alignment_rationale": "<1-2 sentences>",
  "delivery_model": "<FUNDER|IMPLEMENTER|HYBRID|UNCLEAR>",
  "delivery_model_evidence": "<sentence>",
  "spend": {{"inr_crore": <number or null>, "display": "", "fiscal_year": "", "has_disclosed_budget": <bool>, "confidence": <0-100>, "source_excerpt": "", "source": "", "trend_direction": "<RISING|FLAT|DECLINING|UNKNOWN>", "trend_evidence": "", "history": [{{"fiscal_year": "", "inr_crore": null, "display": "", "source_excerpt": ""}}]}},
  "programmes": [{{"name": "", "description": "", "is_multi_year": <bool>, "cohort_or_scale": "", "source_excerpt": "", "source": ""}}],
  "partners": [{{"name": "", "relationship_type": "", "source_excerpt": "", "source": ""}}],
  "decision_makers": [{{"name": "", "title": "", "public_facing_score": <0-100>, "tenure_status": "<NEW_UNDER_1YR|ESTABLISHED_1_3YR|ENTRENCHED_3YR_PLUS|UNKNOWN>", "tenure_evidence": "", "source_excerpt": "", "source": ""}}],
  "geographies": [{{"place": "", "source_excerpt": "", "source": ""}}],
  "criteria": [
{_criteria_json_template()}
  ],
  "red_flags": [{{"flag": "", "severity": "<low|medium|high>", "explanation": "", "source": ""}}],
  "contact_pathway": {{"channel": "", "evidence": "", "source": ""}},
  "rfp_signal": {{"present": <bool>, "channel": "", "evidence": "", "source": ""}},
  "board_affinity": {{"present": <bool>, "person_name": "", "connection": "", "source_excerpt": "", "source": ""}},
  "volunteering": {{"present": <bool>, "programme_name": "", "description": "", "source_excerpt": "", "source": ""}},
  "group_foundation": {{"routed_through_group": <bool>, "foundation_name": "", "explanation": "", "source_excerpt": "", "source": ""}},
  "eligibility": {{"plausibly_mandated": "<LIKELY|UNLIKELY|UNKNOWN>", "reasoning": "", "net_worth_turnover_signal": "", "source": ""}},
  "sector": {{"sector": "", "sub_sector": "", "reasoning": ""}},
  "evidence_recency": "<sentence>", "csr_head_note": "<sentence>",
  "source_quality_assessment": "<1-2 sentences>",
  "overall_authenticity_score": <int>,
  "open_questions": ["<short item>"]
}}"""


# ── Parse + recover ──────────────────────────────────────────────────────────
def parse_json_response(raw_text) -> dict:
    if not raw_text:
        return {}
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass
    recovered = _recover_partial_json(cleaned)
    if recovered:
        return recovered
    end = max(cleaned.rfind("}"), cleaned.rfind("]"))
    if end == -1:
        return {}
    for off in range(0, 3):
        try:
            parsed = json.loads(cleaned[: end + 1 - off] + "}" * off)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            continue
    return {}


def _recover_partial_json(cleaned: str) -> dict:
    decoder = json.JSONDecoder()
    for cut in range(len(cleaned), 0, -1):
        candidate = cleaned[:cut].rstrip()
        if not candidate:
            continue
        trimmed = candidate.rstrip(",")
        for closers in ("", "}", "]}", "]}}", "}]}", "}]}}"):
            try:
                parsed = decoder.decode(trimmed + closers)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("fit_score") is not None:
                return parsed
        if cut < len(cleaned) - 4000:
            break
    return {}


# ── Highlight markers ────────────────────────────────────────────────────────
_STRAY_MARKER = re.compile(r"\*{3,}")
_DOUBLE_STAR = re.compile(r"\*\*")


def _normalize_highlight_markers(text: str) -> str:
    if not text:
        return text
    cleaned = _STRAY_MARKER.sub("**", text)
    if len(_DOUBLE_STAR.findall(cleaned)) % 2 != 0:
        cleaned = cleaned.replace("**", "")
    return cleaned


def _ensure_single_highlight(text: str, phrase_source: str) -> str:
    if not text or not text.strip() or "**" in text:
        return text
    words = re.findall(r"[A-Za-z][A-Za-z\-']*", phrase_source or "")
    if len(words) < 2:
        return text
    phrase = " ".join(words[:3]) if len(words) >= 3 else " ".join(words[:2])
    pos = text.lower().find(phrase.lower())
    if pos == -1:
        return text
    return text[:pos] + "**" + text[pos:pos + len(phrase)] + "**" + text[pos + len(phrase):]


# ── Repair + sanitize ────────────────────────────────────────────────────────
def _repair_full_analysis(parsed: dict):
    if not isinstance(parsed, dict):
        parsed = {}
    raw = parsed.get("criteria")
    by_id = {}
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict) and e.get("id") in CRITERIA_IDS:
                by_id[e["id"]] = e
    repaired = []
    for cid in CRITERIA_IDS:
        e = by_id.get(cid, {})
        repaired.append({
            "id": cid,
            "score": clamp_float(e.get("score"), 0, 5, 0.0),
            "confidence": clamp_int(e.get("confidence"), 0, 100, 0),
            "evidence": _normalize_highlight_markers(
                (e.get("evidence") or "To confirm — no signal returned by model")[:240]),
            "reasoning": (e.get("reasoning") or "")[:240],
            "source": e.get("source") or "",
        })
    parsed = dict(parsed)
    parsed["criteria"] = repaired
    parsed["fit_score"] = clamp_int(parsed.get("fit_score"), 0, 100, 0)

    for f in ("fit_rationale", "alignment_rationale", "delivery_model_evidence",
              "source_quality_assessment", "csr_head_note", "evidence_recency"):
        if isinstance(parsed.get(f), str):
            parsed[f] = _normalize_highlight_markers(parsed[f])
    if isinstance(parsed.get("contact_pathway"), dict) and \
            isinstance(parsed["contact_pathway"].get("channel"), str):
        parsed["contact_pathway"]["channel"] = _normalize_highlight_markers(
            parsed["contact_pathway"]["channel"])

    try:
        return FullAnalysisSchema.model_validate(parsed)
    except ValidationError:
        for key in ("spend", "contact_pathway", "rfp_signal", "board_affinity",
                    "volunteering", "group_foundation", "eligibility", "sector"):
            parsed[key] = parsed.get(key) if isinstance(parsed.get(key), dict) else {}
        for key in ("programmes", "partners", "decision_makers", "geographies",
                    "red_flags", "open_questions"):
            parsed[key] = parsed.get(key) if isinstance(parsed.get(key), list) else []
        try:
            return FullAnalysisSchema.model_validate(parsed)
        except ValidationError:
            return FullAnalysisSchema(
                fit_score=clamp_int(parsed.get("fit_score"), 0, 100, 0),
                criteria=[CriterionResultSchema(**c) for c in repaired])


def _valid_source_lookup(sources_manifest: str) -> set:
    valid = set()
    for line in sources_manifest.splitlines():
        name = line.split("|")[0].strip()
        if name:
            valid.add(name)
    return valid


def _sanitize_source(value: str, valid: set) -> str:
    cleaned = (value or "").strip()
    return cleaned if cleaned in valid else ""


# ── Consistency backfill (prose facts -> structured fields) ─────────────────
_NEGATIVE_EVIDENCE = re.compile(
    r"^(no |none|not mentioned|not found|to confirm|no direct mention|no clear|"
    r"no signal|no specific|no explicit|unclear|no supporting)", re.IGNORECASE)


def _is_positive_evidence(evidence: str) -> bool:
    t = (evidence or "").strip()
    return bool(t) and not _NEGATIVE_EVIDENCE.match(t)


_TITLE_CASE_PHRASE = re.compile(
    r"\b(?:[A-Z][a-zA-Z&()'\-]*\s+){0,4}[A-Z][a-zA-Z&()'\-]*"
    r"(?:\s+(?:Project|Programme|Program|Academy|Initiative|Mission|Foundation|"
    r"Trust|Fund|Fellowship|Scholarship|Chatbot|Campaign|Labs?))\b")
_GENERIC_STOP = {"csr", "india", "the company", "this company", "tap",
                 "ngo", "ngos", "government", "govt", "delhi", "mumbai",
                 "maharashtra"}
_NGO_FOUNDATION = re.compile(
    r"\b([A-Z][a-zA-Z&.'\-]*(?:\s+[A-Z][a-zA-Z&.'\-]*){0,4}"
    r"\s+(?:Foundation|Trust|NGO|Society|Charitable Trust))\b")
_MONEY = re.compile(
    r"(₹\s?[\d,]+(?:\.\d+)?\s?(?:crore|cr\.?|lakh|lac)|"
    r"(?:INR|Rs\.?)\s?[\d,]+(?:\.\d+)?\s?(?:crore|cr\.?|lakh|lac)?|"
    r"[\d,]+(?:\.\d+)?\s?(?:crore|cr\.?)\b)", re.IGNORECASE)
_PERCENT_MANDATE = re.compile(r"\b(\d(?:\.\d+)?)\s?%\s?(?:csr)?\s?(?:mandate|of)",
                              re.IGNORECASE)
_STATE_NAME = re.compile(
    r"\b(Delhi|Mumbai|Maharashtra|Karnataka|Bengaluru|Bangalore|Tamil Nadu|Chennai|"
    r"Telangana|Hyderabad|Gujarat|Ahmedabad|West Bengal|Kolkata|Uttar Pradesh|"
    r"Rajasthan|Punjab|Haryana|Kerala|Odisha|Bihar|Madhya Pradesh|Pune|"
    r"Andhra Pradesh|Assam|Goa|Chandigarh)\b")


def _extract_named(text: str, pattern, limit: int) -> list:
    seen, out = set(), []
    for m in pattern.finditer(text or ""):
        cand = (m.group(1) if m.groups() else m.group(0)).strip()
        key = cand.lower()
        if len(cand) < 4 or key in seen or key in _GENERIC_STOP:
            continue
        seen.add(key)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def _existing_names(entries: list) -> set:
    return {(e.get("name") or "").strip().lower() for e in entries if e.get("name")}


def _backfill_from_rationale(result: dict, rationale: str) -> None:
    if not rationale or not rationale.strip():
        return
    prog_names = _existing_names(result.get("programmes", []))
    for name in _extract_named(rationale, _TITLE_CASE_PHRASE, 8):
        if name.lower() in prog_names:
            continue
        prog_names.add(name.lower())
        result.setdefault("programmes", []).append({
            "name": name,
            "description": "Referenced in the analysis narrative as a named initiative.",
            "is_multi_year": "multi-year" in rationale.lower(),
            "cohort_or_scale": "", "source_excerpt": "", "source": ""})

    partner_names = _existing_names(result.get("partners", []))
    for name in _extract_named(rationale, _NGO_FOUNDATION, 6):
        if name.lower() in partner_names:
            continue
        partner_names.add(name.lower())
        rel = ("implementer" if re.search(r"implement(ing|ation) partner", rationale, re.I)
               else "funder" if re.search(r"\bfund(s|ed|ing)?\b", rationale, re.I)
               else "unclear")
        result.setdefault("partners", []).append({
            "name": name, "relationship_type": rel,
            "source_excerpt": "", "source": ""})

    spend = result.get("spend") or {}
    if not spend.get("has_disclosed_budget"):
        money, pct = _MONEY.search(rationale), _PERCENT_MANDATE.search(rationale)
        if money or pct:
            if money:
                display = money.group(0).strip()
                if pct:
                    display += f" ({pct.group(1)}% CSR mandate, inferred minimum)"
            else:
                display = f"~{pct.group(1)}% CSR mandate applies (exact spend not disclosed)"
            spend.update({"has_disclosed_budget": True, "display": display,
                          "confidence": spend.get("confidence") or 35,
                          "source_excerpt": "Derived from figures in the analysis narrative."})
            result["spend"] = spend

    geo_names = _existing_names(result.get("geographies", []))
    for place in _extract_named(rationale, _STATE_NAME, 6):
        if place.lower() in geo_names:
            continue
        geo_names.add(place.lower())
        result.setdefault("geographies", []).append({
            "place": place, "source_excerpt": "Mentioned in the analysis narrative.",
            "source": ""})


def _backfill_contact_from_dms(result: dict) -> None:
    cp = result.get("contact_pathway") or {}
    if (cp.get("channel") or "").strip():
        return
    dms = [d for d in result.get("decision_makers", []) if (d.get("name") or "").strip()]
    if not dms:
        return
    dms.sort(key=lambda d: d.get("public_facing_score", 0), reverse=True)
    top = dms[0]
    title = (top.get("title") or "").strip()
    channel = (f"No open call was found; the warmest path is likely a direct "
               f"approach to {top['name']}{f' ({title})' if title else ''} via "
               f"the CSR office.")
    cp["channel"] = _ensure_single_highlight(channel, top["name"])
    result["contact_pathway"] = cp


def _backfill_narrative_gaps(result: dict, company: str, found_count: int) -> dict:
    criteria = result.get("criteria", [])
    usable = [c for c in criteria
              if c.get("confidence", 0) > 0 and _is_positive_evidence(c.get("evidence", ""))]
    strongest = sorted(usable, key=lambda c: c.get("score", 0), reverse=True)

    if not result.get("fit_rationale", "").strip() and strongest:
        top = strongest[0]["name"]
        result["fit_rationale"] = _ensure_single_highlight(
            f"Based on the available evidence, {company}'s strongest signal is "
            f"{top.lower()}. This score reflects the balance of signals rather "
            f"than a single deciding factor.", top)

    for narrative in (result.get("fit_rationale", ""), result.get("csr_head_note", ""),
                      result.get("alignment_rationale", ""),
                      result.get("delivery_model_evidence", ""),
                      *[c.get("evidence", "") for c in criteria]):
        _backfill_from_rationale(result, narrative)

    _backfill_contact_from_dms(result)

    if not result.get("source_quality_assessment", "").strip():
        result["source_quality_assessment"] = (
            "Findings draw primarily on company-published material and press "
            "coverage; figures should be **checked against source** before use."
            if found_count else
            "No usable public sources were fetched, so **no verified evidence** "
            "underlies this analysis.")

    if not result.get("evidence_recency", "").strip():
        result["evidence_recency"] = (
            "The fetched sources do not consistently state a publication date; "
            "treat recency as an **unconfirmed detail** until checked.")

    if result.get("overall_semantic_alignment", 0) == 0 and usable:
        core = {"education_intervention", "stem", "tech_21cs",
                "public_schooling", "systems_change"}
        rel = [c for c in usable if c.get("id") in core] or usable
        result["overall_semantic_alignment"] = clamp_int(
            round(sum(c["score"] for c in rel) / len(rel) / 5 * 100), 0, 100, 0)

    if result.get("overall_authenticity_score", 0) == 0 and found_count > 0:
        result["overall_authenticity_score"] = 55

    return result


# ── Decision-maker matcher — CURRENT roles only ──────────────────────────────
def _people_match_prompt(company: str, raw_hits_text: str) -> str:
    return f"""Identify which LinkedIn results are CURRENT CSR / sustainability / foundation decision-makers at {company}, using ONLY the snippets below.

HITS (each line: subject_name | title | url | snippet):
\"\"\"
{raw_hits_text[:3500]}
\"\"\"

Rules:
- The person MUST currently hold the role AT {company}. If the snippet shows "former", "ex-", "until 20XX", "previously", "alumni", or a past end-date, set is_current_csr_role=false and cap match_confidence at 25.
- Use ONLY the subject_name as the person — never a name that appears only inside the snippet body (those are "People also viewed" sidebar entries, not this profile's owner).
- Prefer India-based roles. If the role is clearly at a different company, drop it.
- Only include linkedin_url if a literal linkedin.com/in/ URL is in that hit.
- tenure_status from date language: NEW_UNDER_1YR / ESTABLISHED_1_3YR / ENTRENCHED_3YR_PLUS / UNKNOWN.

Return ONLY valid JSON:
{{"people": [{{"name": "<subject name>", "title": "<current title>", "is_current_csr_role": <bool>, "match_confidence": <0-100>, "linkedin_url": "<url or empty>", "tenure_status": "<NEW_UNDER_1YR|ESTABLISHED_1_3YR|ENTRENCHED_3YR_PLUS|UNKNOWN>", "reasoning": "<short>"}}]}}"""


def match_people_from_search(company: str, hits: list):
    """
    Filters raw people-search hits down to CURRENT CSR decision-makers only.
    Returns [] silently if the LLM is unavailable — callers keep their
    keyword-extracted list (marked unverified) in that case.
    """
    if not analysis_enabled() or not hits:
        return []
    lines = []
    for h in hits[:20]:
        subject = (h.get("subject_name") or "").strip()
        title   = (h.get("title") or "").strip()
        url     = (h.get("url") or h.get("href") or "").strip()
        snippet = (h.get("snippet") or h.get("body") or "").strip()
        if not (subject or title or snippet):
            continue
        lines.append(f"{subject} | {title} | {url} | {snippet[:180]}")
    raw = "\n".join(lines)
    if not raw.strip():
        return []

    reply = call_anthropic_chat(_people_match_prompt(company, raw),
                                max_tokens=1000, temperature=0.0,
                                caller=f"match_people:{company}")
    parsed = parse_json_response(reply)
    people = parsed.get("people") if isinstance(parsed, dict) else None
    if not isinstance(people, list):
        return []

    out = []
    for p in people:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        ten = p.get("tenure_status", "UNKNOWN")
        out.append({
            "name": name,
            "title": (p.get("title") or "").strip()[:90],
            "is_current_csr_role": bool(p.get("is_current_csr_role")),
            "match_confidence": clamp_int(p.get("match_confidence"), 0, 100, 0),
            "linkedin_url": (p.get("linkedin_url") or "").strip(),
            "tenure_status": ten if ten in {"NEW_UNDER_1YR", "ESTABLISHED_1_3YR",
                                            "ENTRENCHED_3YR_PLUS", "UNKNOWN"} else "UNKNOWN",
            "reasoning": (p.get("reasoning") or "").strip()[:160],
        })
    out.sort(key=lambda x: x["match_confidence"], reverse=True)
    # Keep only people the model judged CURRENT with reasonable confidence.
    return [p for p in out if p["is_current_csr_role"] and p["match_confidence"] >= 50][:8]


# ── Entry point ──────────────────────────────────────────────────────────────
def analyze_company(company: str, mission: str, sources: list,
                    sources_manifest: str, temperature: float = 0.0):
    """Full Claude analysis. Returns dict or None (silent degradation)."""
    if not analysis_enabled():
        return None

    structured = structure_all_sources(sources, company)
    if not structured:
        logger.info("analysis skipped company=%r reason=no_relevant_evidence", company)
        return None

    tpm_limit = _tpm_limit()
    scaffold = estimate_tokens(
        full_company_analysis_prompt(company, mission, "", sources_manifest)
    ) + SCAFFOLD_SAFETY_MARGIN
    input_budget = max(MIN_EVIDENCE_BUDGET,
                       tpm_limit - OUTPUT_TOKEN_RESERVE - scaffold)

    evidence = build_token_budgeted_evidence(structured, company, input_budget)
    if not evidence.strip():
        return None

    prompt = full_company_analysis_prompt(company, mission, evidence, sources_manifest)
    ceiling = tpm_limit - OUTPUT_TOKEN_RESERVE
    attempts = 0
    while estimate_tokens(prompt) > ceiling and input_budget > MIN_EVIDENCE_BUDGET and attempts < 6:
        input_budget = max(MIN_EVIDENCE_BUDGET,
                           input_budget - (estimate_tokens(prompt) - ceiling) - 120)
        evidence = build_token_budgeted_evidence(structured, company, input_budget)
        prompt = full_company_analysis_prompt(company, mission, evidence, sources_manifest)
        attempts += 1
    if estimate_tokens(prompt) > ceiling:
        logger.error("analysis prompt would not fit TPM budget company=%r", company)
        return None

    raw = call_anthropic_chat(prompt, max_tokens=OUTPUT_TOKEN_RESERVE,
                              temperature=temperature,
                              caller=f"analyze_company:{company}")
    if raw is None:
        return None

    parsed = parse_json_response(raw)
    validated = _repair_full_analysis(parsed)
    result = validated.model_dump()

    valid_sources = _valid_source_lookup(sources_manifest)
    by_id = {c["id"]: c for c in result["criteria"] if c["id"] in CRITERIA_IDS}
    ordered = []
    for cid in CRITERIA_IDS:
        m = by_id.get(cid)
        ordered.append({
            "id": cid, "name": CRITERIA_TITLES[cid],
            "score": clamp_float(m.get("score") if m else None, 0, 5, 0.0),
            "confidence": clamp_int(m.get("confidence") if m else None, 0, 100, 0),
            "evidence": ((m.get("evidence") if m else "").strip()
                         or "To confirm — no signal returned by model")[:240],
            "reasoning": ((m.get("reasoning") if m else "") or "").strip()[:240],
            "source": _sanitize_source(m.get("source") if m else "", valid_sources),
        })
    result["criteria"] = ordered

    for key in ("delivery_model_source",):
        result[key] = _sanitize_source(result.get(key, ""), valid_sources)
    for coll in ("programmes", "partners", "decision_makers", "geographies", "red_flags"):
        for entry in result.get(coll, []):
            entry["source"] = _sanitize_source(entry.get("source", ""), valid_sources)
    for key in ("contact_pathway", "rfp_signal", "board_affinity",
                "volunteering", "group_foundation", "eligibility"):
        result[key]["source"] = _sanitize_source(result[key].get("source", ""), valid_sources)
    result["spend"]["source"] = _sanitize_source(result["spend"].get("source", ""), valid_sources)
    result["spend"]["trend_source"] = _sanitize_source(
        result["spend"].get("trend_source", ""), valid_sources)
    for e in result["spend"].get("history", []):
        e["source"] = _sanitize_source(e.get("source", ""), valid_sources)

    result["fit_score"] = clamp_int(result.get("fit_score"), 0, 100, 0)
    result["overall_semantic_alignment"] = clamp_int(
        result.get("overall_semantic_alignment"), 0, 100, 0)
    result["overall_authenticity_score"] = clamp_int(
        result.get("overall_authenticity_score"), 0, 100, 0)
    result["open_questions"] = [q.strip()[:200] for q in result.get("open_questions", [])
                                 if q and q.strip()][:5]
    result["llm_fallback_used"] = not bool(parsed)

    found_count = sum(1 for s in sources if s.get("status") == "FOUND")
    result = _backfill_narrative_gaps(result, company, found_count)
    return result
