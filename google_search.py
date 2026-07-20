# google_search.py — Google Custom Search with quota guard (ported from fork)
# Used as the primary search provider; scraper falls back to DuckDuckGo when
# the key/engine-id is absent or the daily quota is spent.
import logging
import os
import time

import requests

logger = logging.getLogger("tap.google_search")

GOOGLE_SEARCH_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
DAILY_CAP = int(os.getenv("GOOGLE_SEARCH_DAILY_CAP", "90"))

_usage = {"day": "", "count": 0}


def configured() -> bool:
    return bool(os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
                and os.getenv("GOOGLE_SEARCH_ENGINE_ID", "").strip())


def _has_quota() -> bool:
    today = time.strftime("%Y-%m-%d")
    if _usage["day"] != today:
        _usage["day"], _usage["count"] = today, 0
    return _usage["count"] < DAILY_CAP


def _record_usage():
    _usage["count"] += 1


def available() -> bool:
    return configured() and _has_quota()


def search(query: str, max_results: int = 5) -> list:
    """
    Returns results normalised to the ddgs shape:
      [{"title": ..., "href": ..., "body": ...}, ...]
    Returns [] on any failure so callers can fall back to DuckDuckGo.
    """
    if not available():
        return []
    try:
        _record_usage()
        resp = requests.get(
            GOOGLE_SEARCH_ENDPOINT,
            params={
                "key": os.getenv("GOOGLE_SEARCH_API_KEY", "").strip(),
                "cx": os.getenv("GOOGLE_SEARCH_ENGINE_ID", "").strip(),
                "q": query,
                "num": min(max_results, 10),
                "gl": "in",
            },
            timeout=12,
        )
        if resp.status_code == 429:
            logger.warning("google search 429 — quota exhausted, marking day spent")
            _usage["count"] = DAILY_CAP
            return []
        resp.raise_for_status()
        items = resp.json().get("items", []) or []
        return [{"title": it.get("title", ""),
                 "href": it.get("link", ""),
                 "body": it.get("snippet", "")}
                for it in items[:max_results]]
    except Exception as exc:
        logger.warning("google search failed q=%r err=%s", query[:60], exc)
        return []
