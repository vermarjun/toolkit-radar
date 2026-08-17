"""Pass 3b: arbitrate between what the model already knew and what it just read.

This pass exists because the naive version of this pipeline was measurably worse
than doing nothing. Grounded retrieval scored 57% against the gold set where the
closed-book control scored 68%, and the critic on top of it dragged the result to
37%. The cause was a single instruction in the extraction prompt — *where the
evidence is silent, do not fall back on what you remember* — combined with a
corpus cut to two pages per app by a credit budget. Silence was frequent, and
every silence threw away a prior that was right about seven times in ten.

The fix is not more retrieval. It is to stop treating evidence and prior as
rivals. Evidence *updates* a prior; it does not replace it. That is what a human
researcher does with a half-loaded documentation page, and it is what this does:

    1. the evidence is silent            -> keep the prior
    2. evidence and prior agree          -> keep it, and be confident
    3. the critic doubts the evidence    -> fall back to the prior
    4. otherwise                         -> evidence wins

Rule 3 is the inverse of the original design. A claim the critic cannot support
in a two-page corpus is *unsupported*, which is not the same as *wrong*;
demoting it to `unknown` destroyed information, while falling back to the prior
keeps the best answer anyone in the loop has.

The critic is also **not allowed to propose a replacement value**, though it is
asked for one and its suggestion is kept in the record. Graded against the gold
set, its corrections were right 27% of the time — worse than leaving the field
alone. It is a good detector of "this page does not say that" and a bad source
of what the page says instead, so it now only casts doubt. That restriction was
chosen from a measurement on 20 apps and could be an artefact of a small sample;
it is stated here rather than buried because it is the kind of decision that
should be re-measured on more data before anyone leans on it.

Every field records which rule fired, so the page can show provenance per cell
rather than asking anyone to take this on trust.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from agent.schema import normalise

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FIELDS = ["primary_auth", "access", "api_surface", "has_mcp"]
UNKNOWN = {"UNKNOWN", "unknown", None}


def _precritic(row: dict) -> dict:
    """Undo the critic's demotions to recover what retrieval alone produced."""
    out = dict(row)
    for ch in row.get("critic_changes") or []:
        out[ch["field"]] = ch["from"]
    return out


def _critic_verdict(row: dict, field: str) -> tuple[bool, str | None]:
    """(refuted, correction) as the critic saw it, before any demotion applied."""
    audit = row.get("critic_audit") or {}
    v = audit.get(field)
    if not isinstance(v, dict):
        return False, None
    if v.get("supported") is not False:
        return False, None
    c = v.get("correction")
    if c in (None, "", "null"):
        return True, None
    return True, c


def arbitrate_one(prior: dict, grounded_row: dict) -> dict:
    ground = _precritic(grounded_row)
    out = dict(grounded_row)
    provenance: dict[str, str] = {}
    conf = dict(out.get("confidence") or {})

    for f in FIELDS:
        p_val = prior.get(f)
        g_val = ground.get(f)
        refuted, correction = _critic_verdict(grounded_row, f)

        if g_val in UNKNOWN and p_val not in UNKNOWN:
            value, rule = p_val, "prior_fills_silence"
            conf[f] = min(float((prior.get("confidence") or {}).get(f, 0.5)), 0.6)
        elif g_val == p_val:
            value, rule = g_val, "agreed"
            conf[f] = max(float(conf.get(f, 0.6)), 0.85)
        else:
            value, rule = g_val, "evidence_overrides_prior"
            conf[f] = max(float(conf.get(f, 0.6)), 0.7)

        out[f] = value
        provenance[f] = rule

    # verdict is derived, not retrieved: recompute it from the arbitrated fields
    # so it cannot contradict them.
    out["verdict"] = derive_verdict(out)
    provenance["verdict"] = "derived"
    out["confidence"] = conf
    out["provenance"] = provenance
    return out


def derive_verdict(f: dict) -> str:
    """A verdict that disagrees with its own row is worse than no verdict.

    The models were asked for this field directly and produced answers that
    contradicted the access and surface values sitting beside them. It is a
    function of the other fields, so it is computed rather than asked for.
    """
    access, surface = f.get("access"), f.get("api_surface")
    documented = surface in {"rest", "graphql", "rest+graphql", "soap", "rpc"}

    if surface == "none" or access == "no_public_api":
        return "not_buildable"
    if surface == "sdk_only":
        # A CLI or a library is wrappable, but it is not an API: no tenant, no
        # credential, and a different build shape from every other row here.
        return "build_with_caveats"
    if access in {"approval_required", "partner_gated", "plan_gated"}:
        # The gate is on getting a credential, not on the interface. With public
        # docs the toolkit can still be written against a customer's own tenant,
        # so this is a testing problem. Without them there is nothing to write.
        return "build_with_caveats" if documented else "needs_outreach"
    if access in {"self_serve_free", "self_serve_paid"}:
        return "build_now" if documented else "build_with_caveats"
    return "unknown"


def main() -> None:
    prior = {r["id"]: r for r in json.loads((DATA / "pass1.json").read_text())}
    grounded = json.loads((DATA / "pass2.json").read_text())
    out = [arbitrate_one(prior.get(r["id"], {}), r) for r in grounded]
    (DATA / "pass3.json").write_text(json.dumps(out, indent=2))

    tally = Counter(rule for r in out for rule in r["provenance"].values())
    print(f"arbitrated {len(out)} apps -> data/pass3.json")
    for rule, n in tally.most_common():
        print(f"   {rule:<30} {n}")


if __name__ == "__main__":
    main()
