# utils.py — shared utilities for TAP Research Agent
"""
Core building block: EvidencedFact
Every single data point extracted by the agent must be an EvidencedFact.
This enforces zero-hallucination — if there is no source, there is no fact.
"""

import re
import requests
from dataclasses import dataclass, field, asdict
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ─────────────────────────────────────────────────────────────────────────────
# EvidencedFact — the zero-hallucination contract
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvidencedFact:
    """
    Every extracted data point must carry its source evidence.
    If confidence is None or source_url is empty, the fact is NOT shown in output.

    Confidence levels:
      HIGH   — exact quoted text matched (e.g. "₹42 crore CSR spend")
      MEDIUM — strong inference from multiple signals (e.g. "education" in context)
      LOW    — implied or assumed — NOT included in final output
    """
    value:       str
    confidence:  str        # "HIGH" | "MEDIUM" | "LOW"
    source_url:  str
    source_type: str        # india_csr_page | mca_portal | national_csr_portal | annual_report | web_snippet
    excerpt:     str        # verbatim text snippet supporting this fact (max 300 chars)
    field_name:  str = ""   # which field this fact supports

    def is_verified(self) -> bool:
        """Returns True only if this fact meets publication bar."""
        return (
            self.confidence in ("HIGH", "MEDIUM")
            and bool(self.source_url.strip())
            and bool(self.excerpt.strip())
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP session
# ─────────────────────────────────────────────────────────────────────────────

def get_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Source record
# ─────────────────────────────────────────────────────────────────────────────

def make_source(
    name: str, priority: int,
    url: str = "", text: str = "",
    status: str = "NOT_FOUND", fetch_method: str = "search"
) -> dict:
    return {
        "source_name": name, "priority": priority,
        "url": url, "text": text,
        "status": status, "fetch_method": fetch_method,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3-state helpers
# ─────────────────────────────────────────────────────────────────────────────

def all_sources_tried(sources: list) -> bool:
    return len(sources) >= 4

def any_source_found(sources: list) -> bool:
    return any(s.get("status") == "FOUND" for s in sources)

def best_source_quality(sources: list, quality_cfg: dict) -> tuple:
    best_score, best_name = 0, "none"
    for s in sources:
        if s.get("status") == "FOUND":
            sn = s.get("source_name", "")
            q  = quality_cfg.get(sn, 3)
            if q > best_score:
                best_score, best_name = q, sn
    return (best_score or 3), (best_name or "web_search_snippet")


# ─────────────────────────────────────────────────────────────────────────────
# Text utilities
# ─────────────────────────────────────────────────────────────────────────────

def clean_text(text: str, max_chars: int = 15000) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]

def window(text: str, pos: int, before: int = 150, after: int = 200) -> str:
    return text[max(0, pos - before): pos + after].strip()

def combine_source_texts(sources: list) -> str:
    return "\n\n".join(
        s["text"] for s in sources
        if s.get("status") == "FOUND" and s.get("text")
    )

def to_json(obj) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)

def excerpt(text: str, pos: int, chars: int = 280) -> str:
    """Clean verbatim excerpt centred on a position — for EvidencedFact."""
    raw = window(text, pos, chars // 2, chars // 2)
    return re.sub(r"\s+", " ", raw).strip()[:chars]
