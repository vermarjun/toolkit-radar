"""Turn 100 findings into a ranked build queue.

The assignment asks for a buildability verdict. A verdict sorts apps into four
piles; it does not say which of the 41 uncovered apps to build on Monday. This
module is the opinionated half: a transparent, hand-weighted score so the ranking
can be argued with rather than trusted.

Every weight is a product judgement and is stated in `WEIGHTS`. Nothing here is
learned or fitted — with 100 rows and no outcome data, a fitted model would be
false precision.
"""

from __future__ import annotations

WEIGHTS = {
    "credential_access": 40,  # can an engineer get a working key today
    "api_surface": 25,        # is there enough there to be worth wrapping
    "auth_effort": 15,        # how much integration work the scheme implies
    "evidence": 10,           # how sure we are of the row at all
    "agent_readiness": 10,    # a vendor MCP means the surface is already shaped
}

ACCESS_POINTS = {
    "self_serve_free": 40,
    "self_serve_paid": 32,
    "plan_gated": 20,
    "approval_required": 12,
    "partner_gated": 4,
    "no_public_api": 0,
    "unknown": 10,
}

BREADTH_POINTS = {"broad": 25, "moderate": 16, "narrow": 8, "none": 0, "unknown": 10}

# Lower effort scores higher. OAuth is not "hard" for Composio specifically —
# managed auth is their product — but it still means an app registration, a
# redirect URI, and a review in some cases, so it is not free.
AUTH_POINTS = {
    "API_KEY": 15, "BEARER_TOKEN": 15, "NO_AUTH": 15,
    "BASIC": 13, "OAUTH2": 10, "S2S_OAUTH2": 9,
    "JWT": 7, "AWS_SIGV4": 5, "UNKNOWN": 5,
}

EFFORT_RULES = [
    ("S", lambda f: f["primary_auth"] in {"API_KEY", "BEARER_TOKEN", "BASIC", "NO_AUTH"}
                    and f["api_breadth"] in {"narrow", "moderate"}),
    ("L", lambda f: f["primary_auth"] in {"AWS_SIGV4", "JWT", "S2S_OAUTH2"}
                    or f["api_breadth"] == "broad"),
]


def effort(f: dict) -> str:
    for tier, rule in EFFORT_RULES:
        try:
            if rule(f):
                return tier
        except Exception:
            continue
    return "M"


def score_one(f: dict) -> dict:
    conf = f.get("confidence") or {}
    mean_conf = sum(conf.values()) / len(conf) if conf else 0.4

    parts = {
        "credential_access": ACCESS_POINTS.get(f.get("access"), 10),
        "api_surface": 0 if f.get("api_surface") in {"none", "unknown"} else BREADTH_POINTS.get(f.get("api_breadth"), 10),
        "auth_effort": AUTH_POINTS.get(f.get("primary_auth"), 5),
        "evidence": round(mean_conf * WEIGHTS["evidence"], 1),
        "agent_readiness": 10 if f.get("has_mcp") else 0,
    }
    total = round(sum(parts.values()), 1)
    return {"build_score": total, "score_parts": parts, "effort": effort(f)}


LANE_RULES = """
build_now         score >= 70 and access is self-serve      -> an engineer starts today
quick_win         score >= 55 and effort S                  -> a day, not a sprint
needs_outreach    access is approval_required/partner_gated -> partnerships, not engineering
park              no usable public API, or nothing found    -> revisit when it changes
"""


def lane(f: dict, scored: dict) -> str:
    access = f.get("access")
    if access in {"approval_required", "partner_gated"}:
        return "needs_outreach"
    if access == "no_public_api" or f.get("api_surface") == "none":
        return "park"
    if scored["build_score"] >= 70 and access in {"self_serve_free", "self_serve_paid"}:
        return "build_now"
    if scored["build_score"] >= 55 and scored["effort"] == "S":
        return "quick_win"
    if scored["build_score"] >= 55:
        return "build_now"
    return "park"


def enrich(findings: list[dict]) -> list[dict]:
    out = []
    for f in findings:
        s = score_one(f)
        out.append({**f, **s, "lane": lane(f, s)})
    return out
