"""The research agent itself: four passes over the same 100 apps.

    pass 1  closed-book   model answers from parametric memory, no tools
    pass 2  grounded      search -> scrape docs -> extract with citations
    pass 3  critic        second model checks every claim against the fetched
                          text and demotes anything it cannot find support for
    pass 4  gate prover   a real browser tries the signup / dev-portal flow for
                          the commercial-access field (agent/gate.py)

Pass 1 exists only so the report can show what the naive version of this
assignment scores. It is the control, not a stage.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agent import search as fc
from agent.gateway import USAGE, chat, extract_json
from agent.schema import (
    ACCESS,
    API_SURFACE,
    AUTH_METHODS,
    BREADTH,
    VERDICT,
    normalise,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

EXTRACT_MODEL = "deepseek-v4-flash"
CRITIC_MODEL = "kimi-k2.6"      # different family from the extractor, on purpose
CRITIC_FALLBACK = "minimax-m3"  # kimi 503s under concurrency often enough to matter
EXTRACT_FALLBACK = "glm-5.1"    # for the ~8% of calls deepseek returns empty on

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


SCHEMA_BLOCK = f"""Return ONE JSON object, no prose, no markdown fence, with exactly these keys:

  "one_liner":    string, <=140 chars, what the product does. No marketing words.
  "auth_methods": array from {AUTH_METHODS} — every scheme the public API accepts.
  "primary_auth": string from {AUTH_METHODS} — the one a third-party integration
                  would actually use to act on behalf of a customer.
  "access":       string from {ACCESS} — the commercial gate on getting a working
                  credential as an outside developer:
                    self_serve_free   sign up free/trial, self-issue a credential
                    self_serve_paid   anyone can buy online, no human involved
                    plan_gated        needs a specific higher paid tier
                    approval_required form, app review, or manual enablement
                    partner_gated     partnership or contact-sales only
                    no_public_api     no third-party API exists
  "access_note":  string, <=200 chars, the specific gate. Name the plan or the form.
  "api_surface":  string from {API_SURFACE}
  "api_breadth":  string from {BREADTH} — broad = most objects CRUD-able,
                  narrow = a handful of endpoints.
  "has_mcp":      true / false — an MCP server PUBLISHED BY THE VENDOR of this
                  app. A community server on GitHub does not count, nor does a
                  third party's hosted MCP that happens to wrap this product.
                  false if you are not sure.
  "mcp_note":     string, <=140 chars, whose MCP and where.
  "verdict":      string from {VERDICT}
                    build_now          public docs + self-serve credential today
                    build_with_caveats buildable, with a real annoyance (SigV4,
                                       per-tenant hosts, paid-only sandbox)
                    needs_outreach     API exists but the credential needs a
                                       human at the vendor
                    not_buildable      no usable third-party API
  "blocker":      string, <=200 chars. Empty if none.
  "evidence":     array of 1-4 absolute URLs that actually support the answers.
  "confidence":   object with a 0.0-1.0 number for each of
                  "primary_auth", "access", "api_surface", "has_mcp", "verdict".
                  Be honest. Low confidence is useful; a confident guess is not.
"""


# --------------------------------------------------------------------------- #
# pass 1 — closed book                                                          #
# --------------------------------------------------------------------------- #

def _extract_with_fallback(prompt: str, max_tokens: int) -> dict:
    """deepseek-v4-flash returns an empty completion on roughly 1 call in 12,
    consistently for the same input, so retrying it is pointless. Hand those to a
    second model rather than dropping the app."""
    try:
        return extract_json(chat(prompt, model=EXTRACT_MODEL, max_tokens=max_tokens))
    except Exception:
        return extract_json(chat(prompt, model=EXTRACT_FALLBACK, max_tokens=max_tokens))


def pass1_one(app: dict) -> dict:
    prompt = f"""You are cataloguing SaaS APIs for an agent-tooling platform.

App: {app['app']}
Category: {app['category']}
Hint: {app['hint']}

Answer from your own knowledge. You have no browser and no search. If you do not
know a field, say so through a low confidence number and the "unknown" enum
member rather than guessing.

{SCHEMA_BLOCK}"""
    raw = _extract_with_fallback(prompt, max_tokens=1400)
    out = normalise(raw if isinstance(raw, dict) else {})
    out["evidence"] = []  # closed-book URLs are recalled, not verified
    return out


# --------------------------------------------------------------------------- #
# pass 2 — grounded                                                             #
# --------------------------------------------------------------------------- #

DOC_HOST_HINTS = ("docs.", "developer.", "developers.", "/docs", "/api", "/developer", "api.")


def _pick_pages(results: list[dict], limit: int) -> list[str]:
    """Prefer documentation URLs over marketing pages, keep source order otherwise."""
    scored = []
    for i, r in enumerate(results):
        url = r["url"]
        score = sum(2 for h in DOC_HOST_HINTS if h in url.lower())
        low = (r["title"] + " " + r["description"]).lower()
        score += 2 * sum(1 for w in ("authentication", "api key", "oauth", "rest api", "getting started") if w in low)
        score -= i * 0.1
        scored.append((score, url))
    seen, picked = set(), []
    for _, url in sorted(scored, key=lambda x: -x[0]):
        host_path = url.split("#")[0]
        if host_path in seen:
            continue
        seen.add(host_path)
        picked.append(host_path)
        if len(picked) >= limit:
            break
    return picked


def gather(app: dict, pages: int = 2, per_query: int = 2) -> dict:
    """Retrieval step. Returns the corpus handed to the extractor."""
    name, hint = app["app"], app["hint"]
    domain = re.split(r"[ (]", hint)[0]
    # Three queries, each aimed at one field the first pass was measurably bad at.
    # The MCP query was added after the eval showed has_mcp was the worst field:
    # a docs-authentication search almost never surfaces an MCP announcement.
    queries = [
        f"{name} API documentation authentication",
        f"{name} developer API access requirements pricing plan",
        f"{name} MCP server model context protocol",
    ]
    # Firecrawl bills one credit per search *result*, so width is the expensive
    # dimension, not the number of queries. Three narrow queries beat one wide
    # one: each targets a different field, and the result descriptions alone
    # answer a surprising share of them without a fetch at all.
    results: list[dict] = []
    for q in queries:
        results.extend(fc.search(q, limit=per_query))

    urls = _pick_pages(results, pages)
    if domain and not any(domain.split("/")[0] in u for u in urls):
        urls.append(f"https://{domain}" if not domain.startswith("http") else domain)

    docs = []
    for u in urls[:pages]:
        md = fc.scrape(u)
        if md.strip():
            docs.append({"url": u, "markdown": md})

    return {
        "search_results": results,
        "fetched": docs,
        "fetched_urls": [d["url"] for d in docs],
    }


def pass2_one(app: dict, corpus: dict) -> dict:
    snippets = "\n\n".join(
        f"### SEARCH RESULT: {r['title']}\nURL: {r['url']}\n{r['description']}"
        for r in corpus["search_results"][:8]
    )
    pages = "\n\n".join(
        f"### FETCHED PAGE\nURL: {d['url']}\n\n{d['markdown'][:9000]}"
        for d in corpus["fetched"]
    )
    prompt = f"""You are cataloguing SaaS APIs for an agent-tooling platform.

App: {app['app']}
Category: {app['category']}
Hint: {app['hint']}

Below is retrieved evidence. Base every answer on it. Where the evidence is
silent, use the "unknown" member and a low confidence number — do NOT fall back
on what you remember about this product. Every URL you put in "evidence" must
appear verbatim in the evidence below.

Pay particular attention to the commercial gate. Docs rarely state it outright;
infer it from phrases like "contact sales", "available on the Enterprise plan",
"submit an app for review", "request access", or from a pricing page that shows
API access only on a specific tier. If a developer can sign up and self-issue a
token, that is self_serve_free even when the product itself is expensive.

======================= EVIDENCE =======================
{snippets}

{pages}
========================================================

{SCHEMA_BLOCK}"""
    raw = _extract_with_fallback(prompt, max_tokens=1800)
    out = normalise(raw if isinstance(raw, dict) else {})
    allowed = set(corpus["fetched_urls"]) | {r["url"] for r in corpus["search_results"]}
    kept = [u for u in out["evidence"] if u in allowed]
    out["evidence"] = kept or corpus["fetched_urls"][:2]
    out["fabricated_citations"] = len(out.get("evidence", [])) and len(
        [u for u in raw.get("evidence", []) or [] if isinstance(u, str) and u not in allowed]
    )
    return out


# --------------------------------------------------------------------------- #
# pass 3 — critic                                                               #
# --------------------------------------------------------------------------- #

CRITIC_FIELDS = ["primary_auth", "access", "api_surface", "has_mcp"]


def critic_one(app: dict, finding: dict, corpus: dict) -> dict:
    """Adversarial check. Returns {field: {supported, reason, correction}}."""
    pages = "\n\n".join(
        f"### {d['url']}\n{d['markdown'][:7000]}" for d in corpus["fetched"]
    )
    snippets = "\n".join(
        f"- {r['url']} :: {r['description'][:300]}" for r in corpus["search_results"][:8]
    )
    claims = {f: finding[f] for f in CRITIC_FIELDS}
    prompt = f"""You are auditing another agent's research on {app['app']}. Your job is
to REFUTE, not to agree. A claim counts as supported only if the evidence below
states or directly implies it. "It is probably true of products like this" is NOT
support.

CLAIMS: {json.dumps(claims)}

======================= EVIDENCE =======================
{snippets}

{pages}
========================================================

Return ONE JSON object keyed by the four claim names. Each value:
  {{"supported": true|false,
    "reason": "<=120 chars, quote or point at the evidence",
    "correction": "<the right value from the same vocabulary, or null>"}}

Vocabularies: primary_auth {AUTH_METHODS}; access {ACCESS}; api_surface {API_SURFACE}; has_mcp true/false.
If unsupported and the evidence does not settle it, set correction to null — the
value will be demoted to unknown, which is the honest outcome."""
    # The critic must not be the model that produced the finding, or it just
    # agrees with itself. Fall through a second family if the first is down —
    # gateway models go 503 under concurrency often enough to matter.
    last = ""
    for model in (CRITIC_MODEL, CRITIC_FALLBACK):
        try:
            raw = extract_json(chat(prompt, model=model, max_tokens=1200, attempts=5))
            if isinstance(raw, dict):
                raw["_critic_model"] = model
                return raw
        except Exception as exc:
            last = f"{model}: {type(exc).__name__}: {exc}"[:200]
    return {"_error": last}


UNKNOWN_FOR = {
    "primary_auth": "UNKNOWN",
    "access": "unknown",
    "api_surface": "unknown",
    "has_mcp": None,
}


def apply_critic(finding: dict, audit: dict) -> dict:
    """Apply the audit. Records every change so the report can show the deltas."""
    out = dict(finding)
    changes = []
    for field in CRITIC_FIELDS:
        verdict = audit.get(field)
        if not isinstance(verdict, dict) or verdict.get("supported") is not False:
            continue
        before = out[field]
        correction = verdict.get("correction")
        if correction not in (None, "", "null"):
            after = normalise({field: correction}).get(field)
        else:
            after = UNKNOWN_FOR[field]
        if after == before:
            continue
        out[field] = after
        conf = out.setdefault("confidence", {})
        conf[field] = min(float(conf.get(field, 0.5)), 0.45)
        changes.append(
            {"field": field, "from": before, "to": after, "reason": (verdict.get("reason") or "")[:160]}
        )
    out["critic_changes"] = changes
    return out


# --------------------------------------------------------------------------- #
# orchestration                                                                 #
# --------------------------------------------------------------------------- #

def load_apps() -> list[dict]:
    import csv

    with (DATA / "apps_100.csv").open() as f:
        return list(csv.DictReader(f))


WORKERS = int(os.environ.get("RADAR_WORKERS", "10"))


def run_pass1(apps: list[dict], workers: int = WORKERS) -> list[dict]:
    out: list[dict] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(pass1_one, a): a for a in apps}
        for fut in as_completed(futs):
            app = futs[fut]
            try:
                finding = fut.result()
            except Exception as exc:
                log(f"  !! pass1 {app['app']}: {exc}")
                finding = normalise({})
                finding["error"] = str(exc)[:200]
            out.append({"id": int(app["id"]), "app": app["app"], "category": app["category"], **finding})
            log(f"  p1 {len(out):>3}/{len(apps)}  {app['app']}")
    out.sort(key=lambda r: r["id"])
    log(f"pass1 done in {time.time()-started:.0f}s")
    return out


def run_pass2(apps: list[dict], workers: int = WORKERS) -> list[dict]:
    out: list[dict] = []
    started = time.time()

    def one(app: dict) -> dict:
        corpus = gather(app)
        finding = pass2_one(app, corpus)
        audit = critic_one(app, finding, corpus)
        finding = apply_critic(finding, audit)
        finding["retrieval"] = {
            "queries": 2,
            "results": len(corpus["search_results"]),
            "pages_fetched": corpus["fetched_urls"],
        }
        finding["critic_audit"] = audit
        return finding

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one, a): a for a in apps}
        for fut in as_completed(futs):
            app = futs[fut]
            try:
                finding = fut.result()
            except Exception as exc:
                log(f"  !! pass2 {app['app']}: {type(exc).__name__}: {exc}")
                finding = normalise({})
                finding["error"] = f"{type(exc).__name__}: {exc}"[:200]
            out.append({"id": int(app["id"]), "app": app["app"], "category": app["category"], **finding})
            log(f"  p2 {len(out):>3}/{len(apps)}  {app['app']}")
    out.sort(key=lambda r: r["id"])
    log(f"pass2 done in {time.time()-started:.0f}s  firecrawl={fc.stats()}")
    return out


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    apps = load_apps()
    if len(sys.argv) > 2:  # optional slice for smoke tests, e.g. `... pass2 5`
        apps = apps[: int(sys.argv[2])]

    if which in ("pass1", "both"):
        log(f"== pass 1 (closed book) over {len(apps)} apps")
        rows = run_pass1(apps)
        (DATA / "pass1.json").write_text(json.dumps(rows, indent=2))

    if which in ("pass2", "both"):
        log(f"== pass 2 (grounded + critic) over {len(apps)} apps")
        rows = run_pass2(apps)
        (DATA / "pass2.json").write_text(json.dumps(rows, indent=2))

    (DATA / "usage.json").write_text(
        json.dumps({"llm": USAGE.snapshot(), "firecrawl": fc.stats()}, indent=2)
    )
    log(json.dumps(USAGE.snapshot(), indent=2))


if __name__ == "__main__":
    main()
