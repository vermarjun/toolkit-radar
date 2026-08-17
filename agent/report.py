"""Merge every artefact into the single JSON the report page and the MCP read.

Inputs : data/pass1.json, pass2.json, gate.json, catalog_join.json, eval.json,
         usage.json, composio_catalog.json
Outputs: site/data.json     one object, the machine-readable twin of the page
         site/findings.csv  the flat table, for anyone who wants a spreadsheet
         site/llms.txt      a text brief so an agent can orient without the JSON
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

from agent.score import ACCESS_POINTS, WEIGHTS, enrich

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"
SITE.mkdir(exist_ok=True)

SELF_SERVE = {"self_serve_free", "self_serve_paid"}
GATED = {"plan_gated", "approval_required", "partner_gated", "no_public_api"}


def _load(name: str, default=None):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default


def build() -> dict:
    findings = _load("pass3.json") or _load("pass2.json", [])
    join = _load("catalog_join.json", {"rows": []})
    gate = {g["id"]: g for g in _load("gate.json", [])}
    evaluation = _load("eval.json", {})
    usage = _load("usage.json", {})
    catalog = _load("composio_catalog.json", [])
    reach = {r["id"]: r for r in _load("reach.json", [])}

    join_by_id = {r["id"]: r for r in join.get("rows", [])}
    rows = enrich(findings)
    for r in rows:
        j = join_by_id.get(r["id"], {})
        r["composio_slug"] = j.get("composio_slug")
        r["composio_auth_schemes"] = j.get("composio_auth_schemes")
        r["composio_tools_count"] = j.get("composio_tools_count")
        r["composio_adjacent"] = j.get("composio_adjacent")
        r["in_composio"] = bool(j.get("composio_slug"))
        g = gate.get(r["id"])
        if g:
            r["browser_access"] = g.get("browser_access")
            r["browser_evidence"] = [
                {k: p.get(k) for k in ("url", "signal", "quotes", "screenshot", "status")}
                for p in g.get("pages", [])
                if p.get("signal") or p.get("screenshot")
            ]
            # The browser can prove a gate; it cannot prove the absence of one.
            # Agreement is therefore only scored on gate-positive findings.
            b = g.get("browser_access")
            r["browser_agrees"] = (
                r["access"] in GATED if b in {"approval_required", "plan_gated", "partner_gated"}
                else None
            )
        rc = reach.get(r["id"])
        if rc:
            r["reachability"] = rc.get("reachability")
            r["probe_url"] = rc.get("probe_url")
            r["probe"] = rc.get("probe")

    by_cat: dict[str, list] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)

    def pct(n, d):
        return round(100 * n / d, 1) if d else 0.0

    n = len(rows)
    auth_mix = Counter(r["primary_auth"] for r in rows)
    access_mix = Counter(r["access"] for r in rows)
    lane_mix = Counter(r["lane"] for r in rows)
    verdict_mix = Counter(r["verdict"] for r in rows)

    catalog_auth = Counter()
    for t in catalog:
        for s in t.get("auth_schemes") or ["UNKNOWN"]:
            catalog_auth[s] += 1

    matrix = []
    for cat, items in by_cat.items():
        ss = sum(1 for r in items if r["access"] in SELF_SERVE)
        matrix.append(
            {
                "category": cat,
                "n": len(items),
                "self_serve": ss,
                "gated": sum(1 for r in items if r["access"] in GATED),
                "self_serve_pct": pct(ss, len(items)),
                "mcp": sum(1 for r in items if r["has_mcp"]),
                "mcp_pct": pct(sum(1 for r in items if r["has_mcp"]), len(items)),
                "in_composio": sum(1 for r in items if r["in_composio"]),
                "median_score": round(
                    sorted(x["build_score"] for x in items)[len(items) // 2], 1
                ),
                "top_auth": Counter(r["primary_auth"] for r in items).most_common(1)[0][0],
            }
        )
    matrix.sort(key=lambda m: -m["self_serve_pct"])

    gap = [r for r in rows if not r["in_composio"]]
    queue = sorted(gap, key=lambda r: -r["build_score"])

    blockers = Counter(r["access"] for r in rows if r["access"] in GATED)

    reach_mix = Counter(r.get("reachability") for r in rows if r.get("reachability"))
    live = [r for r in rows if r.get("reachability") == "live_and_gated"]
    browser_positive = [
        r for r in rows
        if r.get("browser_access") in {"approval_required", "plan_gated", "partner_gated"}
    ]

    return {
        "meta": {
            "n_apps": n,
            "generated_from": "agent/research.py pass 2 -> agent/arbitrate.py pass 3",
            "composio_catalog_size": len(catalog),
            "usage": usage,
        },
        "headline": {
            "self_serve_pct": pct(sum(1 for r in rows if r["access"] in SELF_SERVE), n),
            "gated_pct": pct(sum(1 for r in rows if r["access"] in GATED), n),
            "mcp_pct": pct(sum(1 for r in rows if r["has_mcp"]), n),
            "in_composio": sum(1 for r in rows if r["in_composio"]),
            "gap": len(gap),
            "buildable_gap": sum(1 for r in gap if r["lane"] in {"build_now", "quick_win"}),
            "outreach_gap": sum(1 for r in gap if r["lane"] == "needs_outreach"),
            "top_auth": auth_mix.most_common(1)[0] if auth_mix else None,
            "top_blocker": blockers.most_common(1)[0] if blockers else None,
            "apis_touched_live": len(live),
        },
        "loops": {
            "reachability": {
                "mix": reach_mix.most_common(),
                "live_and_gated": len(live),
                "corroborates_surface": sum(
                    1 for r in live if r["api_surface"] not in {"none", "unknown"}
                ),
                "probed": sum(1 for r in rows if r.get("reachability")),
            },
            "browser": {
                "probed": sum(1 for r in rows if r.get("browser_access") is not None
                              or r.get("browser_evidence")),
                "gate_found": len(browser_positive),
                "confirmed_agent": sum(1 for r in browser_positive if r["browser_agrees"]),
                "corrected_agent": sum(1 for r in browser_positive if r["browser_agrees"] is False),
                "no_gate_found": sum(1 for r in rows if r.get("browser_access") == "no_gate_found"),
            },
        },
        "distributions": {
            "auth": auth_mix.most_common(),
            "access": access_mix.most_common(),
            "lane": lane_mix.most_common(),
            "verdict": verdict_mix.most_common(),
            "blockers": blockers.most_common(),
            "composio_catalog_auth": catalog_auth.most_common(),
        },
        "matrix": matrix,
        "build_queue": [
            {
                k: r[k]
                for k in (
                    "id", "app", "category", "build_score", "effort", "lane",
                    "primary_auth", "access", "api_surface", "api_breadth",
                    "has_mcp", "verdict", "blocker", "evidence", "score_parts",
                )
            }
            for r in queue
        ],
        "rows": rows,
        "eval": evaluation,
        "scoring_model": {"weights": WEIGHTS, "access_points": ACCESS_POINTS},
    }


CSV_COLS = [
    "id", "app", "category", "one_liner", "primary_auth", "auth_methods", "access",
    "access_note", "api_surface", "api_breadth", "has_mcp", "verdict", "blocker",
    "build_score", "effort", "lane", "in_composio", "composio_slug",
    "browser_access", "reachability", "probe_url", "evidence",
]


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(
            {
                **r,
                "auth_methods": "|".join(r.get("auth_methods") or []),
                "evidence": " ".join(r.get("evidence") or []),
            }
        )
    return buf.getvalue()


def to_llms_txt(d: dict) -> str:
    h = d["headline"]
    lines = [
        "# Toolkit Radar: can these 100 apps become agent toolkits today?",
        "",
        "Research output for a Composio AI Product Ops take-home. An agent researched",
        "100 SaaS apps, then got graded against 20 apps a human labelled by hand and",
        "against Composio's own production toolkit catalog.",
        "",
        "## Headline",
        f"- {h['self_serve_pct']}% let an outside developer issue their own credential.",
        f"- {h['gated_pct']}% are gated by a plan, an approval, or a sales call.",
        f"- {h['mcp_pct']}% already ship a vendor-published MCP server.",
        f"- {h['in_composio']} of the 100 are already Composio toolkits. {h['gap']} are not.",
        f"- Of that gap, {h['buildable_gap']} are buildable now and {h['outreach_gap']} need partnerships, not engineers.",
        "",
        "## Machine-readable data",
        "- data.json    every field for every app, plus the eval and the scoring model",
        "- findings.csv the flat table",
        "",
        "## Field vocabulary",
        "primary_auth: OAUTH2 S2S_OAUTH2 API_KEY BEARER_TOKEN BASIC JWT AWS_SIGV4 NO_AUTH UNKNOWN",
        "access: self_serve_free self_serve_paid plan_gated approval_required partner_gated no_public_api unknown",
        "lane: build_now quick_win needs_outreach park",
        "",
        "## How much to trust it",
    ]
    ev = d.get("eval") or {}
    if ev.get("pass1") and ev.get("pass2"):
        lines += [
            f"- closed-book baseline: {ev['pass1']['accuracy']:.1%} on 100 hand-checked labels",
            f"- the shipped pipeline: {ev['pass2']['accuracy']:.1%} on the same labels",
        ]
    if ev.get("oracle"):
        lines.append(
            f"- agrees with Composio's shipped auth config on {ev['oracle']['agreement']:.1%} "
            f"of {ev['oracle']['n_checked']} apps it already covers"
        )
    lines += ["", "Every row carries its evidence URLs. The rows the agent got wrong are listed on the page too."]
    return "\n".join(lines) + "\n"


def render(d: dict) -> None:
    """Inline the dataset into the page so index.html is self-contained."""
    tpl = (SITE / "template.html").read_text()
    payload = json.dumps(d, separators=(",", ":")).replace("</", "<\\/")
    (SITE / "index.html").write_text(tpl.replace("__DATA__", payload))


def main() -> None:
    d = build()
    repo = (ROOT / ".repo_url").read_text().strip() if (ROOT / ".repo_url").exists() else None
    d["links"] = {"repo": repo}
    (SITE / "data.json").write_text(json.dumps(d, indent=2))
    (SITE / "findings.csv").write_text(to_csv(d["rows"]))
    (SITE / "llms.txt").write_text(to_llms_txt(d))
    render(d)
    h = d["headline"]
    print(json.dumps(h, indent=2))
    print(f"\nwrote site/index.html ({(SITE/'index.html').stat().st_size//1024} KB), "
          f"data.json, findings.csv, llms.txt")


if __name__ == "__main__":
    main()
