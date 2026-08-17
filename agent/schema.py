"""The finding schema, and the normalisation that makes model output comparable.

Free-text answers cannot be scored against a gold set, so every judgement field
is a closed vocabulary. The models do not reliably emit the exact enum member, so
``coerce_*`` maps the common near-misses back. Anything unmappable becomes
``unknown`` rather than being silently guessed — an honest ``unknown`` is a
correct answer to grade, a fabricated ``OAUTH2`` is not.
"""

from __future__ import annotations

AUTH_METHODS = [
    "OAUTH2",       # 3-legged user consent
    "S2S_OAUTH2",   # client-credentials / machine token
    "API_KEY",      # static key in a header or query param
    "BEARER_TOKEN", # long-lived personal/admin token
    "BASIC",        # username:password or key:x over basic
    "JWT",          # signed assertion the caller mints
    "AWS_SIGV4",    # request signing
    "NO_AUTH",      # public, no credential
    "UNKNOWN",
]

# The commercial gate, which is the field docs never state outright.
ACCESS = [
    "self_serve_free",      # sign up, get a credential, free tier or trial
    "self_serve_paid",      # anyone can buy it online, no human in the loop
    "plan_gated",           # needs a specific (usually enterprise) paid tier
    "approval_required",    # form / review / manual enablement by the vendor
    "partner_gated",        # partnership or contact-sales only
    "no_public_api",        # there is no API for third parties at all
    "unknown",
]

API_SURFACE = ["rest", "graphql", "rest+graphql", "soap", "sdk_only", "rpc", "none", "unknown"]
BREADTH = ["broad", "moderate", "narrow", "none", "unknown"]
VERDICT = ["build_now", "build_with_caveats", "needs_outreach", "not_buildable", "unknown"]

# Fields that are scored against the gold set.
GRADED_FIELDS = ["primary_auth", "access", "api_surface", "has_mcp", "verdict"]

_AUTH_ALIASES = {
    "oauth": "OAUTH2", "oauth2": "OAUTH2", "oauth 2.0": "OAUTH2", "oauth2.0": "OAUTH2",
    "oauth_2": "OAUTH2", "three-legged oauth": "OAUTH2", "3-legged oauth": "OAUTH2",
    "client_credentials": "S2S_OAUTH2", "client credentials": "S2S_OAUTH2",
    "service account": "S2S_OAUTH2", "machine-to-machine": "S2S_OAUTH2", "m2m": "S2S_OAUTH2",
    "s2s": "S2S_OAUTH2", "s2s_oauth2": "S2S_OAUTH2",
    "apikey": "API_KEY", "api key": "API_KEY", "api_key": "API_KEY", "key": "API_KEY",
    "token": "BEARER_TOKEN", "bearer": "BEARER_TOKEN", "bearer token": "BEARER_TOKEN",
    "personal access token": "BEARER_TOKEN", "pat": "BEARER_TOKEN",
    "basic auth": "BASIC", "basic": "BASIC", "http basic": "BASIC",
    "jwt": "JWT", "json web token": "JWT",
    "sigv4": "AWS_SIGV4", "aws signature": "AWS_SIGV4", "aws_sigv4": "AWS_SIGV4",
    "none": "NO_AUTH", "no auth": "NO_AUTH", "no_auth": "NO_AUTH", "public": "NO_AUTH",
}

_ACCESS_ALIASES = {
    "self-serve": "self_serve_free", "self serve": "self_serve_free",
    "free": "self_serve_free", "self_serve": "self_serve_free",
    "free tier": "self_serve_free", "trial": "self_serve_free",
    "paid": "self_serve_paid", "paid self-serve": "self_serve_paid",
    "self_serve_paid_plan": "self_serve_paid",
    "enterprise": "plan_gated", "enterprise plan": "plan_gated",
    "paid plan required": "plan_gated", "plan-gated": "plan_gated", "gated": "plan_gated",
    "approval": "approval_required", "application required": "approval_required",
    "review": "approval_required", "app review": "approval_required",
    "waitlist": "approval_required",
    "partner": "partner_gated", "partnership": "partner_gated",
    "contact sales": "partner_gated", "contact-sales": "partner_gated",
    "sales gated": "partner_gated",
    "no api": "no_public_api", "none": "no_public_api",
}

_SURFACE_ALIASES = {
    "rest api": "rest", "restful": "rest", "http": "rest", "json": "rest",
    "graphql api": "graphql", "gql": "graphql",
    "rest and graphql": "rest+graphql", "both": "rest+graphql",
    "grpc": "rpc", "json-rpc": "rpc", "xml-rpc": "rpc",
    "sdk": "sdk_only", "library": "sdk_only", "cli": "sdk_only",
}

_VERDICT_ALIASES = {
    "yes": "build_now", "buildable": "build_now", "build now": "build_now",
    "easy": "build_now", "ready": "build_now",
    "yes_with_caveats": "build_with_caveats", "caveats": "build_with_caveats",
    "partial": "build_with_caveats", "maybe": "build_with_caveats",
    "outreach": "needs_outreach", "needs outreach": "needs_outreach",
    "partnership": "needs_outreach", "contact sales": "needs_outreach",
    "no": "not_buildable", "blocked": "not_buildable", "impossible": "not_buildable",
}


def _coerce(value, allowed: list[str], aliases: dict[str, str], fallback: str):
    if value is None:
        return fallback
    raw = str(value).strip()
    up, low = raw.upper(), raw.lower()
    if up in allowed:
        return up
    if low in allowed:
        return low
    if low in aliases:
        return aliases[low]
    for key, mapped in aliases.items():  # substring, last resort
        if key in low:
            return mapped
    return fallback


def coerce_auth(v) -> str:
    return _coerce(v, AUTH_METHODS, _AUTH_ALIASES, "UNKNOWN")


def coerce_access(v) -> str:
    return _coerce(v, ACCESS, _ACCESS_ALIASES, "unknown")


def coerce_surface(v) -> str:
    return _coerce(v, API_SURFACE, _SURFACE_ALIASES, "unknown")


def coerce_breadth(v) -> str:
    return _coerce(v, BREADTH, {"large": "broad", "wide": "broad", "small": "narrow", "limited": "narrow"}, "unknown")


def coerce_verdict(v) -> str:
    return _coerce(v, VERDICT, _VERDICT_ALIASES, "unknown")


def coerce_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"true", "yes", "y", "1"}:
        return True
    if s in {"false", "no", "n", "0", "none"}:
        return False
    return None


def normalise(raw: dict) -> dict:
    """Coerce one raw model finding into the graded schema."""
    auths = raw.get("auth_methods") or []
    if isinstance(auths, str):
        auths = [a for a in auths.replace("/", ",").split(",") if a.strip()]
    auths = [coerce_auth(a) for a in auths]
    auths = [a for a in dict.fromkeys(auths) if a != "UNKNOWN"]

    primary = coerce_auth(raw.get("primary_auth"))
    if primary == "UNKNOWN" and auths:
        primary = auths[0]
    if primary != "UNKNOWN" and primary not in auths:
        auths.insert(0, primary)

    conf = raw.get("confidence") or {}
    if not isinstance(conf, dict):
        conf = {}

    return {
        "one_liner": (raw.get("one_liner") or "").strip()[:220],
        "auth_methods": auths or ["UNKNOWN"],
        "primary_auth": primary,
        "access": coerce_access(raw.get("access")),
        "access_note": (raw.get("access_note") or "").strip()[:300],
        "api_surface": coerce_surface(raw.get("api_surface")),
        "api_breadth": coerce_breadth(raw.get("api_breadth")),
        "has_mcp": coerce_bool(raw.get("has_mcp")),
        "mcp_note": (raw.get("mcp_note") or "").strip()[:200],
        "verdict": coerce_verdict(raw.get("verdict")),
        "blocker": (raw.get("blocker") or "").strip()[:300],
        "evidence": [u for u in (raw.get("evidence") or []) if isinstance(u, str) and u.startswith("http")][:6],
        "confidence": {k: v for k, v in conf.items() if isinstance(v, (int, float))},
    }
