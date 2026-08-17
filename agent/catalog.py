"""Join the 100-app research set against Composio's live toolkit catalog.

Two jobs:

1. **Gap analysis** — which of the 100 already exist as a Composio toolkit, so
   the output is a build queue rather than a list.
2. **Independent oracle** — for apps Composio already ships, its production
   ``auth_schemes`` are a human-maintained answer to "what auth does this app
   use", produced by a different process than our agent. That gives a second,
   much larger validation set than the 20-app hand sample.

Matching is deliberately conservative: an alias table for the cases where the
research-set name and the toolkit slug legitimately differ, then exact/normalised
slug equality. Fuzzy matches are reported as *candidates* for a human to confirm,
never auto-accepted — a wrong join would poison the oracle.
"""

from __future__ import annotations

import csv
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "composio_catalog.json"
APPS = ROOT / "data" / "apps_100.csv"

# Research-set name -> Composio toolkit slug. Every entry here was eyeballed
# against the catalog record; see docs/JOIN_NOTES.md for the ones that are
# judgement calls.
ALIASES = {
    "Lark (Larksuite)": "lark",
    "Magento (Adobe Commerce)": "adobe_commerce",
    "Threads (Meta)": "threads",
    "Meta Ads": "facebook_ads",
    "WhatsApp Business": "whatsapp",
    "Monday.com": "monday",
    "systeme.io": "systeme_io",
    "Amazon Selling Partner": "amazon_selling_partner",
    "Salesforce Commerce Cloud": "salesforce_commerce_cloud",
    "Google Ads": "google_ads",
    "LinkedIn Ads": "linkedin_ads",
    "Zoho CRM": "zoho_crm",
    "Zoho Cliq": "zoho_cliq",
    "Help Scout": "helpscout",
    "MongoDB Atlas": "mongodb",
    "Otter AI": "otter_ai",
    "Bright Data": "brightdata",
    "SE Ranking": "se_ranking",
    "YouTube Transcript": "youtube_transcript",
    "NotebookLM": "notebooklm",
    # Confirmed by reading the toolkit description, not by string similarity.
    "Pylon": "pylon_mcp",
    "GoHighLevel": "highlevel",
    "Devin": "devin_mcp",
    "Zoho CRM": "zoho",
}

# Composio ships something *related* but not the same product. Counted as a gap,
# flagged so the report does not claim coverage it does not have.
ADJACENT = {
    "Mermaid CLI": ("mermaid_chart_mcp", "MermaidChart's hosted MCP, not the OSS mermaid-cli renderer"),
    "Squarespace": ("square", "Square (payments) is a different company from Squarespace"),
    "YouTube Transcript": ("youtube", "Composio wraps the YouTube Data API, not transcriptapi.com"),
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_catalog() -> list[dict]:
    return json.loads(CATALOG.read_text())


def load_apps() -> list[dict]:
    with APPS.open() as f:
        return list(csv.DictReader(f))


def build_join() -> dict:
    catalog = load_catalog()
    by_slug = {t["slug"]: t for t in catalog}
    by_norm: dict[str, dict] = {}
    for t in catalog:
        by_norm.setdefault(norm(t["slug"]), t)
        by_norm.setdefault(norm(t["name"]), t)

    rows, unmatched = [], []
    for app in load_apps():
        name = app["app"]
        tk = None
        confidence = None
        if name in ALIASES:
            tk = by_slug.get(ALIASES[name])
            confidence = "alias"
        if tk is None:
            tk = by_norm.get(norm(name))
            confidence = "exact" if tk else None
        candidates = []
        if tk is None:
            # Surface near-misses for a human, do not auto-accept.
            scored = sorted(
                (
                    (SequenceMatcher(None, norm(name), norm(t["name"])).ratio(), t)
                    for t in catalog
                ),
                key=lambda x: -x[0],
            )[:3]
            candidates = [
                {"slug": t["slug"], "name": t["name"], "score": round(s, 3)}
                for s, t in scored
                if s > 0.72
            ]
            unmatched.append({"app": name, "candidates": candidates})

        rows.append(
            {
                "id": int(app["id"]),
                "app": name,
                "category": app["category"],
                "hint": app["hint"],
                "composio_slug": tk["slug"] if tk else None,
                "composio_match": confidence,
                "composio_auth_schemes": tk["auth_schemes"] if tk else None,
                "composio_managed_auth": tk.get("composio_managed_auth_schemes") if tk else None,
                "composio_tools_count": (tk.get("meta") or {}).get("tools_count") if tk else None,
                "composio_triggers_count": (tk.get("meta") or {}).get("triggers_count") if tk else None,
                "composio_no_auth": tk.get("no_auth") if tk else None,
                "near_miss_candidates": candidates or None,
                "composio_adjacent": ADJACENT.get(name),
            }
        )

    covered = sum(1 for r in rows if r["composio_slug"])
    return {
        "catalog_size": len(catalog),
        "covered": covered,
        "not_covered": len(rows) - covered,
        "rows": rows,
        "unmatched": unmatched,
    }


if __name__ == "__main__":
    out = build_join()
    (ROOT / "data" / "catalog_join.json").write_text(json.dumps(out, indent=2))
    print(f"catalog: {out['catalog_size']} toolkits")
    print(f"covered: {out['covered']}/100   gap: {out['not_covered']}")
    print("\nNOT in Composio today:")
    for r in out["rows"]:
        if not r["composio_slug"]:
            cands = r["near_miss_candidates"]
            extra = f"   ~ {[c['slug'] for c in cands]}" if cands else ""
            print(f"  {r['id']:>3} {r['app']:<28} [{r['category']}]{extra}")
