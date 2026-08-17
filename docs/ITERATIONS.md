# How accuracy actually moved

A log of what changed the number, kept because the brief asks for the journey and
not just the destination. It is not a story of steady improvement. The middle of
it is a pipeline that was **worse than doing nothing**, and finding that out is
the most useful thing the eval did.

All figures are 100 labels across 20 hand-labelled apps (`evals/gold.csv`).

## Where it ended up

    pass 1  closed book          68%     the control: one model call, no tools
    pass 2  grounded             74%     after the prompt fix below
    pass 2  grounded + critic    42%     the critic, as originally designed
    pass 3  arbitrated           74%     shipped

Net movement: **+6 points**, and a much better-calibrated agent — 88% right on
the labels it called high-confidence versus 66% on the middle band.

## The control: 68%

Pass 1 is one model call per app with no search, no browser, and no evidence.

    pass 1 — closed book        68.0%
      primary_auth  70%   access 70%   api_surface 80%   has_mcp 50%   verdict 70%

Two things were visible immediately:

- **`api_surface` is nearly solved from memory.** Whether Stripe has a REST API
  is in the weights. Spending retrieval budget there would have been waste.
- **`has_mcp` is the worst field by a distance**, and for a reason no amount of
  prompting fixes: nearly every vendor MCP server in this set shipped in 2025 or
  2026. The model is not wrong so much as *out of date*, which is exactly the
  class of question retrieval should own.

## The part that went backwards

The first grounded run scored **56%** — twelve points *below* the control — and
adding the critic on top took it to **36%**. Retrieval made the agent worse, and
verification made it worse again. Both regressions came from decisions that
sounded obviously correct.

**1. The extraction prompt said: *where the evidence is silent, do not fall back
on what you remember*.** That is the standard anti-hallucination instruction and
it is right when the corpus is complete. This corpus was not: the free Firecrawl
tier bills per search *result*, a full-width run was projected to drain the
account at app 55 of 100, and retrieval was cut to two fetched pages per app.
Silence was therefore common, and every silence discarded a prior that was right
about seven times in ten. `primary_auth` fell from 70% to 45%, almost entirely
through newly-introduced `unknown`s.

The fix was to stop framing it as a contest. The prompt now says the evidence
wins where the two *disagree* — vendors change auth schemes faster than training
data moves — but where the evidence is silent the model may answer from its own
knowledge at a capped confidence. A third page per app was added at the same
time, paid for out of the credits the cache had saved.

    pass 2 — grounded, prompt fixed     74%    (+6 over the control)

**2. The critic could demote a claim to `unknown` when it could not support it.**
Across the run it demoted 147 claims, and the fields it touched collapsed:
`primary_auth` to 15%, `access` to 25%.

    pass 2 — grounded + critic          42%    (-32 from the grounded pass)

The critic was not malfunctioning. It was correctly observing that a three-page
corpus does not support most of what was asserted. The error was mine, in
treating *unsupported* as though it meant *wrong*.

## The fix: arbitration (`agent/arbitrate.py`)

Evidence and prior are not rivals. Evidence **updates** a prior; it does not
replace it. Three rules, applied per field, each recorded in `provenance`:

    1. the evidence is silent      -> keep the prior
    2. evidence and prior agree    -> keep it, and raise confidence
    3. otherwise                   -> evidence wins

    pass 3 — arbitrated                 74%    (+6 over the control)

Per-rule hit rates on the gold set are printed by `evals/score.py` and shown on
the report page, because a merge rule nobody can audit is just a different black
box.

### The critic lost both of its jobs to measurement

It was given two, and failed both:

| what it was allowed to do | hit rate on the gold set |
| --- | --- |
| propose a replacement value | **27%** |
| cast doubt, reverting the field to the prior | **31%** (n=16) |

Neither is a close call. A rule that is right 31% of the time is not noise at
n=16, and it was removed on that number rather than on the single label it moved
in the headline.

The critic still runs, and its flags are stored and published per row, because
*"a second model could not find support for this on the page we fetched"* is
genuinely useful to a human deciding what to re-check. It just does not get to
act on that alone. The honest summary is that the adversarial-verification idea,
in both the forms I gave it, cost accuracy — and the arbitration layer's main job
turned out to be undoing it.

### The verdict field is computed, not asked for

`verdict` is a function of `access` and `api_surface`, and the models kept
producing verdicts that contradicted the two fields sitting next to them. It is
now derived in code from the same rule the human labeller used. A verdict that
disagrees with its own row is worse than no verdict.

## What was fixed in the spec, not the model

The first `has_mcp` definition read "published by the vendor **or a well-known
third party**". There is no fact of the matter about "well-known", so a share of
what looked like model error was two parties disagreeing about a definition. The
prompt was tightened to *vendor-published only*, the labelling rule in
`evals/LABELLING.md` was written to match, and **the control was re-run under the
tightened spec** so the comparison is not contaminated by the change.
`data/pass1_loose_spec.json` keeps the original run.

The re-run scored the same 68% overall. The point is not that the spec fix raised
the number — it did not. It made the number *mean* something. On a real
toolkit-research pipeline that is the difference between hiring reviewers and
writing a better field definition.

## What did not work

- **`response_format` for JSON.** The gateway hangs on it outright. JSON is
  enforced by prompt and parsed defensively.
- **Trusting the model's citations.** Early runs cited plausible URLs that were
  never fetched. Citations are now intersected with the retrieved corpus and
  anything invented is dropped.
- **One model for everything.** `deepseek-v4-flash` returns an empty completion
  on roughly one call in twelve, deterministically for a given input, so retrying
  the same model is wasted wall-clock. Those calls fall through to `glm-5.1`.
- **Retry-on-empty.** The first client treated empty completions as transient and
  burned ~90 seconds per occurrence on backoff before failing anyway.
- **Four arbitration policies.** Requiring explicit critic support, requiring
  confidence ≥ 0.8, and treating `has_mcp` asymmetrically all produced *identical*
  gold-set scores to the simple rule. The merge is not the bottleneck; the
  extraction prompt was. Reported because a null result is still a result.
- **Adversarial verification, in every form tried.** See above. It is the most
  recommended technique in the agent-evaluation literature and it lost six points
  here, twice. The lesson is not that critics are useless — it is that a critic
  is only as good as the corpus it audits against, and a rationed corpus makes it
  a machine for manufacturing false doubt.

## What I would do next, in order

1. **A field for *who* the gate is on.** The single biggest remaining error class
   is not knowing whether a vendor gates the *integrator* (LinkedIn approves your
   app — genuine outreach) or the *tenant* (a Gladly admin issues a token —
   nothing to negotiate). Those look identical in the current schema and demand
   opposite responses from a Product Ops team.
2. **Retrieval depth, not retrieval cleverness.** Two to three pages per app is
   the binding constraint and it is a $20/month problem, not a research problem.
3. **A bigger gold set.** 20 apps means one flipped label moves the headline by a
   point. 100 labelled apps would make the per-field numbers worth arguing about.
