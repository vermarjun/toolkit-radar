"""Grounding layer: Firecrawl search + scrape, cached to disk.

Everything is cached by content hash under ``research_cache/`` so a re-run costs
no credits and the whole pipeline is reproducible from a cold checkout without
burning someone else's quota. Cache misses are the only billable events, and
``FIRECRAWL_BUDGET`` caps them so a runaway loop cannot drain the account.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "research_cache"
CACHE.mkdir(exist_ok=True)

BASE = "https://api.firecrawl.dev/v2"
_lock = threading.Lock()
_spent = {"search": 0, "scrape": 0, "cache_hits": 0}
BUDGET = int(os.environ.get("FIRECRAWL_BUDGET", "900"))


def stats() -> dict:
    with _lock:
        return dict(_spent)


def _key(kind: str, payload: dict) -> Path:
    h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
    return CACHE / f"{kind}_{h}.json"


def _spend(kind: str) -> None:
    with _lock:
        total = _spent["search"] + _spent["scrape"]
        if total >= BUDGET:
            raise RuntimeError(f"Firecrawl budget of {BUDGET} calls exhausted")
        _spent[kind] += 1


def _post(path: str, payload: dict, timeout: float = 120.0) -> dict:
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY is not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(f"{BASE}{path}", json=payload, headers=headers)
            if r.status_code == 429:
                time.sleep(min(2**attempt * 5, 60))
                last = RuntimeError("firecrawl 429")
                continue
            if r.status_code >= 400:
                return {"success": False, "error": f"http {r.status_code}: {r.text[:200]}"}
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(min(2**attempt * 2, 20))
    return {"success": False, "error": str(last)}


def search(query: str, limit: int = 5) -> list[dict]:
    """Web search. Returns [{url, title, description}]."""
    payload = {"query": query, "limit": limit}
    path = _key("search", payload)
    if path.exists():
        with _lock:
            _spent["cache_hits"] += 1
        return json.loads(path.read_text())

    _spend("search")
    body = _post("/search", payload)
    results = []
    if body.get("success"):
        for item in (body.get("data") or {}).get("web", []) or []:
            results.append(
                {
                    "url": item.get("url", ""),
                    "title": (item.get("title") or "")[:200],
                    "description": (item.get("description") or "")[:1200],
                }
            )
    path.write_text(json.dumps(results))
    return results


def scrape(url: str, max_chars: int = 14000) -> str:
    """Fetch a page as markdown. Returns '' on failure rather than raising."""
    payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
    path = _key("scrape", payload)
    if path.exists():
        with _lock:
            _spent["cache_hits"] += 1
        return json.loads(path.read_text())[:max_chars]

    _spend("scrape")
    body = _post("/scrape", payload)
    md = ""
    if body.get("success"):
        md = ((body.get("data") or {}).get("markdown") or "")
    path.write_text(json.dumps(md))
    return md[:max_chars]
