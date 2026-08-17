"""Grade the agent against the hand-built gold set, and against Composio's catalog.

Produces `data/eval.json`, which is what the report's verification section reads.
Three separate measurements, deliberately not averaged together:

1. **Accuracy** vs 20 hand-labelled apps. The headline number.
2. **Error taxonomy** — abstain / overclaim / wrong. An agent that says "unknown"
   when it does not know is a different, better failure than one that guesses.
3. **Agreement** vs Composio's production auth config on the 59 apps it already
   ships. Not accuracy (Composio can be terminologically different from a
   vendor's docs), but a much larger consistency check than 20 rows.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIELDS = ["primary_auth", "access", "api_surface", "has_mcp", "verdict"]
UNKNOWNS = {"UNKNOWN", "unknown", None}

# Composio's scheme names -> this project's vocabulary.
COMPOSIO_AUTH_MAP = {
    "OAUTH2": "OAUTH2",
    "DCR_OAUTH": "OAUTH2",
    "OAUTH1": "OAUTH2",
    "S2S_OAUTH2": "S2S_OAUTH2",
    "API_KEY": "API_KEY",
    "BASIC": "BASIC",
    "BEARER_TOKEN": "BEARER_TOKEN",
    "NO_AUTH": "NO_AUTH",
    "BASIC_WITH_JWT": "JWT",
}


def load_gold() -> dict[int, dict]:
    gold = {}
    with (ROOT / "evals" / "gold.csv").open() as f:
        for row in csv.DictReader(f):
            row["has_mcp"] = row["has_mcp"].strip().upper() == "TRUE"
            gold[int(row["id"])] = row
    return gold


def load_pass(name: str) -> dict[int, dict]:
    path = DATA / f"{name}.json"
    if not path.exists():
        return {}
    return {r["id"]: r for r in json.loads(path.read_text())}


def classify(pred, truth) -> str:
    if pred == truth:
        return "correct"
    if pred in UNKNOWNS:
        return "abstained"      # honest miss: said it did not know
    if truth in UNKNOWNS:
        return "overclaimed"    # invented an answer where none is documented
    return "wrong"


def grade(preds: dict[int, dict], gold: dict[int, dict]) -> dict:
    per_field = {f: Counter() for f in FIELDS}
    misses = []
    for app_id, truth in gold.items():
        pred = preds.get(app_id)
        if not pred:
            for f in FIELDS:
                per_field[f]["missing"] += 1
            continue
        for f in FIELDS:
            outcome = classify(pred.get(f), truth[f])
            per_field[f][outcome] += 1
            if outcome != "correct":
                misses.append(
                    {
                        "id": app_id,
                        "app": truth["app"],
                        "field": f,
                        "predicted": pred.get(f),
                        "gold": truth[f],
                        "outcome": outcome,
                        "confidence": (pred.get("confidence") or {}).get(f),
                        "note": truth["note"][:180],
                    }
                )

    total = sum(sum(c.values()) for c in per_field.values())
    correct = sum(c["correct"] for c in per_field.values())
    return {
        "n_apps": len(gold),
        "n_labels": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "per_field": {
            f: {
                "accuracy": round(c["correct"] / max(sum(c.values()), 1), 4),
                **dict(c),
            }
            for f, c in per_field.items()
        },
        "misses": sorted(misses, key=lambda m: (m["field"], m["app"])),
    }


def calibration(preds: dict[int, dict], gold: dict[int, dict]) -> list[dict]:
    """Does a high stated confidence actually mean a higher hit rate?"""
    buckets = defaultdict(lambda: {"n": 0, "correct": 0})
    for app_id, truth in gold.items():
        pred = preds.get(app_id) or {}
        conf = pred.get("confidence") or {}
        for f in FIELDS:
            c = conf.get(f)
            if c is None:
                continue
            key = "0.9-1.0" if c >= 0.9 else "0.7-0.9" if c >= 0.7 else "0.5-0.7" if c >= 0.5 else "<0.5"
            buckets[key]["n"] += 1
            buckets[key]["correct"] += int(classify(pred.get(f), truth[f]) == "correct")
    order = ["0.9-1.0", "0.7-0.9", "0.5-0.7", "<0.5"]
    return [
        {
            "bucket": k,
            "n": buckets[k]["n"],
            "accuracy": round(buckets[k]["correct"] / buckets[k]["n"], 3),
        }
        for k in order
        if buckets[k]["n"]
    ]


def oracle_agreement(preds: dict[int, dict]) -> dict:
    """Cross-check primary_auth against Composio's shipped auth config."""
    join = json.loads((DATA / "catalog_join.json").read_text())
    rows, agree, disagree, abstain = [], 0, 0, 0
    for r in join["rows"]:
        schemes = r["composio_auth_schemes"]
        pred = preds.get(r["id"], {}).get("primary_auth")
        if not schemes or not pred:
            continue
        mapped = {COMPOSIO_AUTH_MAP.get(s, s) for s in schemes}
        if pred in UNKNOWNS:
            abstain += 1
            status = "abstained"
        elif pred in mapped:
            agree += 1
            status = "agrees"
        else:
            disagree += 1
            status = "disagrees"
        rows.append(
            {
                "id": r["id"], "app": r["app"], "predicted": pred,
                "composio": sorted(mapped), "status": status,
            }
        )
    checked = agree + disagree
    return {
        "n_checked": checked,
        "n_abstained": abstain,
        "agreement": round(agree / checked, 4) if checked else 0.0,
        "disagreements": [r for r in rows if r["status"] == "disagrees"],
        "rows": rows,
    }


def main() -> None:
    gold = load_gold()
    p1, p2 = load_pass("pass1"), load_pass("pass2")

    out = {
        "gold_apps": sorted(gold),
        "pass1": grade(p1, gold) if p1 else None,
        "pass2": grade(p2, gold) if p2 else None,
        "calibration_pass2": calibration(p2, gold) if p2 else None,
        "oracle": oracle_agreement(p2) if p2 else None,
    }
    if out["pass1"] and out["pass2"]:
        out["lift"] = round(out["pass2"]["accuracy"] - out["pass1"]["accuracy"], 4)
    (DATA / "eval.json").write_text(json.dumps(out, indent=2))

    def show(name, g):
        if not g:
            return
        print(f"\n{name}: {g['accuracy']:.1%}  ({g['n_labels']} labels over {g['n_apps']} apps)")
        for f, s in g["per_field"].items():
            bits = " ".join(f"{k}={v}" for k, v in s.items() if k != "accuracy" and v)
            print(f"   {f:<14} {s['accuracy']:>6.1%}   {bits}")

    show("PASS 1  closed book", out["pass1"])
    show("PASS 2  grounded + critic", out["pass2"])
    if "lift" in out:
        print(f"\nlift: {out['lift']:+.1%}")
    if out["oracle"]:
        o = out["oracle"]
        print(f"\noracle agreement on primary_auth: {o['agreement']:.1%} of {o['n_checked']} Composio-covered apps "
              f"({o['n_abstained']} abstained)")
    if out["calibration_pass2"]:
        print("\ncalibration (pass 2):")
        for b in out["calibration_pass2"]:
            print(f"   conf {b['bucket']:<9} n={b['n']:<4} accuracy={b['accuracy']:.1%}")


if __name__ == "__main__":
    main()
