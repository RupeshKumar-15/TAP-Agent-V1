# llm.py — semantic alignment scoring via Groq (Llama-3.3-70B)
"""
Adds semantic understanding to the scoring pipeline.

The keyword scorer misses companies whose CSR language doesn't match
config.yaml keywords (e.g. Capgemini's "digital inclusion" = TAP's digital
literacy mission, but scores ~0 on keywords). This module asks an LLM to
read the org mission + the company's actual CSR evidence and judge
alignment by MEANING.

FULL FALLBACK: if GROQ_API_KEY is missing or any call fails, every function
returns None and the rule-based system takes over transparently.

Setup:  add GROQ_API_KEY to .env  (free key: https://console.groq.com)
Config: model + on/off switch live in config.yaml → semantic_scoring
"""

import os
import json
import re
import requests

# Load .env if present (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # dotenv not installed — key must be set as an env var

_GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
_DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


def _chat(prompt: str, max_tokens: int = 600, temperature: float = 0.2,
          json_mode: bool = True, model: str = None) -> str:
    """Single Groq chat call. Returns content string or None on any failure."""
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        return None
    payload = {
        "model": model or _DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        resp = requests.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  [llm] Groq call failed: {e}")
        return None


def _parse_json(raw: str) -> dict:
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Semantic alignment — the Capgemini fix
# ─────────────────────────────────────────────────────────────────────────────

def semantic_alignment(company: str, mission: str, source_text: str) -> dict:
    """
    LLM reads the org mission + company CSR evidence and scores alignment
    by meaning, not keywords.

    Returns {"score": 0-100, "rationale": str, "themes": [..], "flags": [..]}
    or None on any failure (caller falls back to keyword-only scoring).
    """
    if not source_text or not source_text.strip():
        return None

    text_sample = source_text[:6000].strip()

    prompt = f"""You are a senior CSR analyst. Judge how well this company's CSR work aligns with an NGO's mission — by MEANING, not keyword overlap. "Digital inclusion" aligns with digital literacy education even though the words differ.

NGO MISSION:
{mission}

COMPANY: {company}

COMPANY CSR EVIDENCE (from their CSR page / annual report / filings):
\"\"\"
{text_sample}
\"\"\"

Scoring rubric (0-100):
- 85-100: funds programmes that directly match the mission (same skills, same age group, same delivery context)
- 65-84:  strong thematic overlap (e.g. digital literacy / school education / life skills for school-age children)
- 40-64:  adjacent (education or youth broadly, but different focus or age group)
- 15-39:  weak (only tangential, e.g. adult vocational training, university scholarships)
- 0-14:   no meaningful alignment

Strict rules:
- Judge ONLY from the evidence text — never assume programmes not mentioned
- Adult vocational training / job placement is NOT school-age skills education
- University/higher-ed-only programmes score in the weak band
- Thin or vague evidence → be conservative

Return ONLY valid JSON:
{{
  "score": <int 0-100>,
  "rationale": "<2-3 sentences citing specific evidence from the text>",
  "themes": ["<matched themes, max 4>"],
  "flags": ["<misalignment flags like 'vocational_only', 'higher_ed_only', or empty>"]
}}"""

    result = _parse_json(_chat(prompt))
    score = result.get("score")
    if not isinstance(score, (int, float)):
        return None
    result["score"] = int(min(max(score, 0), 100))
    result.setdefault("rationale", "")
    result.setdefault("themes", [])
    result.setdefault("flags", [])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Health check — for the app's AI badge
# ─────────────────────────────────────────────────────────────────────────────

def api_health_check() -> dict:
    """Returns {"ok": bool, "model": str|None, "message": str}."""
    if not os.getenv("GROQ_API_KEY", "").strip():
        return {"ok": False, "model": None,
                "message": "GROQ_API_KEY not set — running in rule-based mode"}
    out = _chat('Reply with JSON: {"status":"ok"}', max_tokens=20)
    if out:
        return {"ok": True, "model": _DEFAULT_MODEL,
                "message": f"Groq connected ({_DEFAULT_MODEL}) — semantic scoring active"}
    return {"ok": False, "model": None,
            "message": "Groq API unreachable — running in rule-based mode"}
