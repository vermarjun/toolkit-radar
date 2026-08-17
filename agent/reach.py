"""Pass 5: touch the API instead of reading about it.

Every other pass in this pipeline reads documentation. Documentation goes stale,
describes endpoints that were deprecated two versions ago, and — for a handful of
apps in this set — describes an API that is not actually exposed to the public
internet at all.

So this one makes a real, unauthenticated request to the documented base URL and
records what comes back. It is not trying to use the API. It is asking one
question: *does this endpoint exist and does it demand a credential?*

    401 / 403          the API is live and gated. The single strongest possible
                       confirmation that both `api_surface` and the auth finding
                       are about something real.
    200                live and answering without a credential
    404 / 405          the host is there but the documented path is not
    dns / timeout      nothing is listening; treat the row with suspicion

No credential is sent and no account is created. One GET per app, to a path the
vendor publishes.

Base URLs are extracted from the corpus that is already on disk — `gather()` is
cache-backed, so re-deriving it costs nothing and no new pages are fetched.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from agent.gateway import extract_json
from agent.research import _extract_with_fallback, gather, load_apps

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
_lock = threading.Lock()


def find_base_url(app: dict, corpus: dict) -> dict:
    pages = "\n\n".join(
        f"### {d['url']}\n{d['markdown'][:6000]}" for d in corpus["fetched"]
    )
    snippets = "\n".join(f"- {r['url']} :: {r['description'][:220]}" for r in corpus["search_results"][:8])
    prompt = f"""From the evidence below, find the base URL of {app['app']}'s public HTTP API
and one GET endpoint that a caller would hit first.

======================= EVIDENCE =======================
{snippets}

{pages}
========================================================

Return ONE JSON object, no prose:
  "base_url":   absolute https URL of the API root, or null if the evidence does
                not show one. Do NOT guess a plausible-looking URL.
  "probe_url":  an absolute URL for a GET endpoint that requires authentication —
                a list or "me"-style endpoint is ideal. null if unknown.
  "per_tenant": true if the host contains a customer-specific subdomain or
                account id, which means no single URL can be probed.
  "note":       <=100 chars.

If the API base is only documented as a template (e.g. https://{{company}}.example.com),
set per_tenant true and probe_url null."""
    try:
        raw = _extract_with_fallback(prompt, max_tokens=400)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:140]}
    if not isinstance(raw, dict):
        return {"error": "non-object response"}
    return raw


def probe(url: str, timeout: float = 15.0) -> dict:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, headers={"Accept": "application/json",
                                    "User-Agent": "toolkit-radar/1.0 (research probe)"})
        body = (r.text or "")[:180]
        return {
            "status": r.status_code,
            "content_type": r.headers.get("content-type", "")[:60],
            "www_authenticate": r.headers.get("www-authenticate", "")[:80] or None,
            "snippet": body.replace("\n", " ")[:160],
        }
    except Exception as exc:
        return {"status": None, "error": f"{type(exc).__name__}: {exc}"[:120]}


def interpret(res: dict) -> str:
    if res.get("status") in (401, 403):
        return "live_and_gated"
    if res.get("status") == 200:
        return "live_open"
    if res.get("status") in (404, 405, 410):
        return "path_not_found"
    if res.get("status") and 500 <= res["status"] < 600:
        return "server_error"
    if res.get("status"):
        return f"http_{res['status']}"
    return "unreachable"


def one(app: dict) -> dict:
    out = {"id": int(app["id"]), "app": app["app"]}
    corpus = gather(app)  # fully cached: costs no credits
    found = find_base_url(app, corpus)
    out.update({k: found.get(k) for k in ("base_url", "probe_url", "per_tenant", "note", "error")})

    url = found.get("probe_url") or found.get("base_url")
    if found.get("per_tenant") or not isinstance(url, str) or not url.startswith("http"):
        out["reachability"] = "not_probeable"
        return out

    res = probe(url)
    out["probe"] = res
    out["reachability"] = interpret(res)
    return out


def main() -> None:
    apps = load_apps()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(one, a): a for a in apps}
        for fut in as_completed(futs):
            app = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"id": int(app["id"]), "app": app["app"],
                     "reachability": "error", "error": f"{type(exc).__name__}: {exc}"[:140]}
            results.append(r)
            with _lock:
                print(f"  reach {len(results):>3}/{len(apps)}  {r['app']:<26} "
                      f"{r.get('reachability')}  {r.get('probe_url') or ''}", flush=True)
    results.sort(key=lambda r: r["id"])
    (DATA / "reach.json").write_text(json.dumps(results, indent=2))

    from collections import Counter
    tally = Counter(r.get("reachability") for r in results)
    print("\n" + "\n".join(f"  {k:<18} {v}" for k, v in tally.most_common()))


if __name__ == "__main__":
    main()
