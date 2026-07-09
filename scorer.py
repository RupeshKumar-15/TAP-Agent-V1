# scorer.py — 6-dimension scoring engine (v5: works with EvidencedFact data)
import os, re, yaml
from utils import all_sources_tried, any_source_found, best_source_quality, combine_source_texts


def _kw_in(text_lower: str, kw: str) -> bool:
    """Word-boundary keyword check (no substring false positives)."""
    return re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])",
                     text_lower) is not None


def _cfg():
    p = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _score_focus(focus_facts: list, cfg: dict) -> tuple:
    """
    STRICT scoring (v6). A single generic keyword must not turn a company
    green — depth of alignment matters:
      - strongest match dominates (70%), average fills in (30%)
      - 1 match → ×0.6 penalty, 2 matches → ×0.85, 3+ → full
      - only generic matches (best < 70 pts) → hard cap of 12/40
    """
    tap_focus = cfg.get("tap_focus_areas", {})
    if not focus_facts:
        return 0, []
    weights, matched = [], []
    for fact in focus_facts:
        kw = fact.get("value","").lower()
        pts = None
        if kw in {k.lower(): v for k, v in tap_focus.items()}:
            pts = {k.lower(): v for k, v in tap_focus.items()}[kw]
        else:
            for k, v in tap_focus.items():
                if k.lower() in kw or kw in k.lower():
                    pts = v
                    break
        if pts is not None:
            weights.append(pts)
            matched.append(f"{fact['value']} ({pts}pts)")
    if not weights:
        return 0, []
    weights.sort(reverse=True)
    best  = weights[0]
    avg   = sum(weights) / len(weights)
    core  = 0.7 * best + 0.3 * avg
    depth = 0.6 if len(weights) == 1 else 0.85 if len(weights) == 2 else 1.0
    score = round(40 * core / 100 * depth)
    if best < 70:               # only generic terms like "education" matched
        score = min(score, 12)
    return min(score, 40), matched


def _score_adjacency(adj_signals: dict, cfg: dict) -> tuple:
    max_boost = cfg.get("max_adjacency_boost", 20)
    fired, total = [], 0
    for cid, info in sorted(adj_signals.items(),
                             key=lambda x: x[1].get("boost",0), reverse=True):
        if info.get("fires") and total < max_boost:
            boost = min(info["boost"], max_boost - total)
            total += boost
            fired.append({
                "id": cid, "label": info["label"],
                "keywords_found": info["keywords_found"],
                "tap_reasoning":  info["tap_reasoning"],
                "boost_applied":  boost,
                "evidence_excerpts": info.get("evidence_excerpts",[]),
            })
    return min(total, max_boost), fired


def _score_geography(geography: list, cfg: dict) -> tuple:
    geo_scores = cfg.get("geo_scores", {})
    tap_states = set(s.lower() for s in cfg.get("tap_states",[]))
    geo_lower  = set(g.get("place","").lower() for g in geography)
    overlap    = geo_lower & tap_states
    if len(overlap) >= 3:
        return geo_scores.get("tap_state_count_3plus", 10), \
               f"{len(overlap)} TAP-presence states"
    if len(overlap) >= 1:
        return geo_scores.get("tap_state_count_1_2", 7), \
               f"TAP state(s): {', '.join(sorted(overlap)).title()}"
    if geo_lower:
        return geo_scores.get("other_india_only", 4), \
               f"India presence ({', '.join(list(geo_lower)[:3]).title()})"
    return geo_scores.get("india_mentioned_only", 2), "India mentioned"


def _score_maturity(sources: list, cfg: dict) -> tuple:
    signals_cfg = cfg.get("maturity_signals", {})
    cap = cfg.get("maturity_cap", 10)
    text = combine_source_texts(sources).lower()
    total, found = 0, []
    for sig, pts in signals_cfg.items():
        if _kw_in(text, sig):
            total += pts
            found.append(sig)
        if total >= cap:
            break
    return min(total, cap), found


def _score_budget(spend: dict, cfg: dict) -> tuple:
    tiers = cfg.get("india_budget_tiers",[])
    unknown_score = cfg.get("budget_unknown_score", 5)
    inr = spend.get("inr_crore")
    if inr is None:
        return unknown_score, "Not publicly disclosed (neutral 5)"
    for tier in sorted(tiers, key=lambda t: t["min_crore"], reverse=True):
        if inr >= tier["min_crore"]:
            return tier["score"], f"{spend['display']} — {tier.get('label','')}"
    return 3, f"{spend['display']}"


def _score_source_quality(sources: list, cfg: dict) -> tuple:
    q_cfg = dict(cfg.get("source_quality", {}))
    q_cfg.setdefault("annual_report", q_cfg.get("global_annual_report", 6))
    return best_source_quality(sources, q_cfg)


def score_band(fit: int, cfg: dict = None) -> dict:
    """Returns the scoring-gradient band for a fit score (strict, config-driven)."""
    cfg = cfg or _cfg()
    for band in cfg.get("score_bands", []):
        if fit >= band.get("min", 0):
            return band
    return {"min": 0, "key": "LOW", "label": "Low fit — deprioritise",
            "color": "#DC2626"}


def determine_state(sources: list) -> str:
    tried = [s for s in sources if s.get("status") != "NOT_TRIED"]
    if any(s.get("status") == "FOUND" for s in sources):
        return "FOUND"
    if len(tried) >= 4:
        return "CONFIRMED_ABSENT"
    if len(tried) > 0:
        return "NOT_FOUND_IN_SOURCE"
    return "NOT_FOUND_IN_SOURCE"


def generate_strategic_insight(company, state, focus_facts, adj_fired, geography,
                               fit_score, delivery=None):
    lines = []
    focus_vals = [f.get("value","") for f in focus_facts]
    fired_labels = [c["label"] for c in adj_fired]
    geo_places = [g.get("place","") for g in geography][:3]

    if state == "CONFIRMED_ABSENT":
        return (
            f"{company} has no publicly available India CSR data across four sources. "
            f"This may indicate no India CSR obligation or undisclosed programmes. "
            f"Recommended: direct outreach to their India office."
        )

    if focus_vals:
        lines.append(
            f"{company} shows CSR alignment with TAP's 21st-century skills mission "
            f"(TAP Buddy: life skills, coding, financial literacy for middle/high-school "
            f"students), with evidence of activity in: {', '.join(focus_vals[:4])}."
        )
    elif fired_labels:
        lines.append(
            f"{company} does not currently fund programmes that exactly match TAP's "
            f"model, but shows investment in adjacent areas: {', '.join(fired_labels[:3])}."
        )
    else:
        lines.append(
            f"Limited public CSR data was found for {company}. "
            f"Data found does not yet confirm direct alignment with TAP's programmes."
        )

    # Delivery model — funder vs implementer is a key pre-sales filter
    if delivery and delivery.get("model") and delivery["model"] != "UNCLEAR":
        lines.append(f"CSR delivery model: {delivery['model']}. {delivery.get('note','')}")

    if adj_fired:
        top = adj_fired[0]
        kws = top["keywords_found"][:3]
        reasoning = top["tap_reasoning"]
        lines.append(
            f"Key adjacency: their work on {', '.join(kws) if kws else top['label']} "
            f"creates a partnership opening. {reasoning}"
        )
        for c in adj_fired:
            if c["id"] == "government_schools":
                lines.append(
                    "Notably, their government school presence means TAP could integrate "
                    "directly into an existing delivery pipeline."
                )
                break

    geo_str = ", ".join(geo_places) if geo_places else "India"
    band = score_band(fit_score)
    lines.append(
        f"Assessment: {band.get('key','')} ({fit_score}/100) — {band.get('label','')}. "
        f"Active in {geo_str}."
    )
    if fit_score >= 80:
        lines.append("This is a partnership-team lead: prepare a tailored pitch showing "
                     "how TAP deepens their existing education investments.")
    elif fit_score >= 65:
        lines.append("Strengthen the case before outreach: verify their current education "
                     "portfolio and identify the warmest introduction path.")
    return " ".join(lines)


def score(company: str, sources: list, parsed: dict) -> dict:
    cfg   = _cfg()
    state = determine_state(sources)

    if state == "CONFIRMED_ABSENT":
        insight = generate_strategic_insight(company, state, [], [], [], 0)
        return {"state": state, "fit_score": 0, "strategic_insight": insight,
                "band": score_band(0, cfg),
                "breakdown": {}, "data": parsed, "sources": sources}

    fa_score,  fa_matched = _score_focus(parsed.get("focus_areas",[]), cfg)
    adj_score, adj_fired  = _score_adjacency(parsed.get("adjacency_signals",{}), cfg)
    geo_score, geo_label  = _score_geography(parsed.get("geography",[]), cfg)
    mat_score, mat_found  = _score_maturity(sources, cfg)
    bud_score, bud_label  = _score_budget(parsed.get("spend",{}), cfg)
    src_score, src_name   = _score_source_quality(sources, cfg)

    total = fa_score + adj_score + geo_score + mat_score + bud_score + src_score
    if state == "NOT_FOUND_IN_SOURCE":
        total = max(total, 10)
    total = min(total, 100)

    breakdown = {
        "focus_alignment":  {"score": fa_score,  "max": 40, "matched": fa_matched,    "label": f"{fa_score}/40"},
        "adjacency_boost":  {"score": adj_score, "max": 20, "fired_clusters": adj_fired, "label": f"{adj_score}/20"},
        "geography_fit":    {"score": geo_score, "max": 10, "label": geo_label},
        "csr_maturity":     {"score": mat_score, "max": 10, "signals": mat_found,     "label": f"{mat_score}/10"},
        "budget_size":      {"score": bud_score, "max": 10, "label": bud_label},
        "source_quality":   {"score": src_score, "max": 10, "source": src_name,       "label": f"{src_score}/10"},
    }

    insight = generate_strategic_insight(
        company, state,
        parsed.get("focus_areas",[]),
        adj_fired,
        parsed.get("geography",[]),
        total,
        delivery=parsed.get("csr_delivery_model"),
    )

    return {"state": state, "fit_score": total, "strategic_insight": insight,
            "band": score_band(total, cfg),
            "breakdown": breakdown, "data": parsed, "sources": sources}
