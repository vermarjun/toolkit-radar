"""Pass 4: prove the commercial gate with a real browser.

`access` is the one field documentation almost never states. Docs describe the
auth scheme; they do not say "and you cannot have a key unless you talk to us".
That answer lives on the pricing page and the developer-portal landing page, in
the shape of the call-to-action.

So: open the pages a developer would actually open, read the *rendered* page
(these are React marketing sites — a plain HTTP fetch returns a shell), classify
the primary call to action, and keep a screenshot as evidence. No account is
created and nothing is submitted anywhere; this only reads public pages.

The classifier is deliberately dumb and rule-based rather than another LLM call.
The whole point of this pass is to be a *different kind* of evidence from the
model that produced the finding — a second language model reading the same web
page is not an independent check.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "site" / "evidence"
SHOTS.mkdir(parents=True, exist_ok=True)

# Ordered: the first pattern that matches wins, strongest gate first.
SIGNALS: list[tuple[str, str, list[str]]] = [
    (
        "partner_gated",
        "the only route to a credential is a human at the vendor",
        [r"contact\s+sales", r"talk\s+to\s+(sales|us)", r"request\s+a\s+demo",
         r"book\s+a\s+demo", r"get\s+a\s+quote", r"custom\s+pricing",
         r"become\s+a\s+partner", r"partner\s+program\s+application"],
    ),
    (
        "approval_required",
        "credentials exist but are released after a review",
        [r"request\s+access", r"apply\s+for\s+access", r"app(lication)?\s+review",
         r"join\s+the\s+waitlist", r"request\s+an\s+invite", r"pending\s+approval",
         r"submit\s+your\s+app", r"business\s+verification"],
    ),
    (
        "plan_gated",
        "the API sits behind a named paid tier",
        [r"available\s+on\s+(the\s+)?(enterprise|business|pro|advanced|scale)\s+plan",
         r"api\s+access.{0,40}(enterprise|business|pro)\s+plan",
         r"(enterprise|business)\s+plan\s+(only|required)",
         r"upgrade\s+to\s+.{0,20}(access|use)\s+the\s+api"],
    ),
    (
        "no_gate_found",
        "a free or trial signup is offered and no gate was detected",
        [r"start\s+(for\s+)?free", r"free\s+trial", r"sign\s+up\s+free",
         r"get\s+started\s+free", r"free\s+forever", r"free\s+plan",
         r"create\s+a\s+free\s+account", r"\$0\s*/", r"try\s+it\s+free"],
    ),
]

CANDIDATE_PATHS = ["/pricing", "/plans", "/pricing/"]


def targets(app: dict, finding: dict) -> list[str]:
    """Pages a developer would actually open to find out if they can get in."""
    hint = re.split(r"[ (]", app["hint"])[0].strip("/")
    host = hint.split("/")[0]
    if not host or "." not in host:
        return []
    urls = [f"https://{host}/pricing"]
    # A doc URL the agent already cited is worth a look: developer-portal landing
    # pages state gates that marketing pricing pages omit.
    for ev in (finding.get("evidence") or [])[:2]:
        p = urlparse(ev)
        if p.scheme and p.netloc:
            urls.append(f"{p.scheme}://{p.netloc}")
    return list(dict.fromkeys(urls))[:2]


def _scan(text: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    counts: dict[str, int] = {}
    quotes: dict[str, list[str]] = {}
    for verdict, _why, patterns in SIGNALS:
        for pat in patterns:
            for m in re.finditer(pat, text):
                counts[verdict] = counts.get(verdict, 0) + 1
                if len(quotes.setdefault(verdict, [])) < 3:
                    s = max(0, m.start() - 70)
                    quotes[verdict].append(text[s : m.end() + 70].strip())
    return counts, quotes


def classify(text: str) -> tuple[str | None, str, list[str]]:
    """Decide the gate from the *balance* of signals, not the first match.

    The first version of this took the strongest matching pattern and was wrong
    on the easiest apps in the set: it called HubSpot and Pipedrive
    ``partner_gated`` because "Contact sales" sits in the navigation bar of very
    nearly every B2B SaaS site, next to the free trial button. The phrase is
    real; it just is not evidence about the API.

    So the order is now: specific gates first (an approval flow or a named plan
    is stated deliberately and rarely by accident), then any credible free-signup
    signal, and only then "contact sales" — which counts as a gate solely when
    nothing on the page offers a way in without one.

    The second lesson was about precision. The gate patterns are specific: nobody
    writes "submit your app for review" by accident. The free-signup patterns are
    not — "Start free" is on the pricing page of almost every product in this set,
    and it describes the *product*, not API access. PitchBook's page says it while
    the API is sold through a rep. So a free CTA no longer resolves to
    ``self_serve_free``; it resolves to ``no_gate_found``, which is all it
    actually licenses anyone to conclude.
    """
    low = re.sub(r"\s+", " ", text.lower())
    counts, quotes = _scan(low)
    why = {v: w for v, w, _ in SIGNALS}

    if counts.get("approval_required"):
        v = "approval_required"
    elif counts.get("plan_gated"):
        v = "plan_gated"
    elif counts.get("no_gate_found"):
        v = "no_gate_found"
    elif counts.get("partner_gated", 0) >= 2:
        # No free CTA anywhere on the page and sales is mentioned repeatedly.
        v = "partner_gated"
    else:
        return None, "", []
    return v, why[v], quotes.get(v, [])[:3]


async def probe_one(browser, app: dict, finding: dict) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", app["app"].lower()).strip("-")
    out = {"id": int(app["id"]), "app": app["app"], "pages": [], "browser_access": None}
    for url in targets(app, finding):
        page = await browser.new_page()
        record = {"url": url}
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=35000)
            record["status"] = resp.status if resp else None
            await page.wait_for_timeout(2500)
            body = await page.inner_text("body")
            verdict, why, hits = classify(body)
            record.update({"signal": verdict, "why": why, "quotes": hits,
                           "final_url": page.url, "chars": len(body)})
            shot = SHOTS / f"{slug}-{urlparse(url).path.strip('/').replace('/', '-') or 'home'}.jpg"
            await page.screenshot(path=str(shot), type="jpeg", quality=55)
            record["screenshot"] = f"evidence/{shot.name}"
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"[:160]
        finally:
            await page.close()
        out["pages"].append(record)

    # Across pages, the same precedence as within one: a deliberately stated gate
    # outranks a free CTA, and a free CTA outranks a bare "contact sales".
    order = ["approval_required", "plan_gated", "partner_gated", "no_gate_found"]
    seen = [p.get("signal") for p in out["pages"] if p.get("signal")]
    if seen:
        out["browser_access"] = sorted(seen, key=order.index)[0]
    return out


async def run(apps: list[dict], findings: dict[int, dict], concurrency: int = 4) -> list[dict]:
    from playwright.async_api import async_playwright

    results: list[dict] = []
    sem = asyncio.Semaphore(concurrency)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
        )

        async def one(app):
            async with sem:
                r = await probe_one(ctx, app, findings.get(int(app["id"]), {}))
                print(f"  gate {r['app']:<26} -> {r['browser_access']}", flush=True)
                return r

        results = await asyncio.gather(*(one(a) for a in apps))
        await ctx.close()
        await browser.close()
    return list(results)


AMBIGUOUS = {"plan_gated", "approval_required", "partner_gated", "unknown"}


def select(apps: list[dict], findings: dict[int, dict], threshold: float = 0.8) -> list[dict]:
    """Only probe where it can change the answer: low confidence or a claimed gate."""
    chosen = []
    for app in apps:
        f = findings.get(int(app["id"]))
        if not f:
            continue
        conf = (f.get("confidence") or {}).get("access", 0.0)
        if f.get("access") in AMBIGUOUS or conf < threshold:
            chosen.append(app)
    return chosen


def main() -> None:
    import agent  # noqa: F401  (loads .env)
    from agent.research import load_apps

    apps = load_apps()
    findings = {r["id"]: r for r in json.loads((ROOT / "data" / "pass3.json").read_text())}
    chosen = select(apps, findings)
    if len(sys.argv) > 1:
        chosen = chosen[: int(sys.argv[1])]
    print(f"probing {len(chosen)} of {len(apps)} apps with a real browser")
    results = asyncio.run(run(chosen, findings))
    (ROOT / "data" / "gate.json").write_text(json.dumps(results, indent=2))

    agreed = sum(
        1 for r in results
        if r["browser_access"] and r["browser_access"] == findings[r["id"]].get("access")
    )
    print(f"\nbrowser signal agreed with the agent on {agreed}/{len(results)}")


if __name__ == "__main__":
    main()
