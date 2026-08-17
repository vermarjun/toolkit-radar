# How accuracy moved

A log of what actually changed the number, kept because the brief asks for the
journey and not just the destination. Each entry is a change, why it was made,
and what it bought.

## The baseline: 68%

Pass 1 is the control — one model call per app, no search, no browser, answering
from what it already knows. It is what this assignment looks like if you skip the
hard half.

    pass 1 (closed book)   68.0%   100 labels / 20 apps
      primary_auth  75%
      access        60%
      api_surface   90%
      has_mcp       55%
      verdict       60%

Three things are visible immediately, and they set the rest of the work:

- **`api_surface` is nearly solved from memory.** Whether Stripe has a REST API
  is in the weights. Spending retrieval budget here would have been waste.
- **`access` and `verdict` are near coin-flips.** These are the commercial
  questions, and they are the ones the assignment is actually about. A model's
  training data contains documentation; it does not contain "and then they made
  the API enterprise-only in March".
- **`has_mcp` was the worst field, and it was my fault.** See below.

## Change 1 — the `has_mcp` spec was undecidable

The first prompt asked for "an MCP server published by the vendor **or a
well-known third party**". There is no fact of the matter about "well-known".
Half the labels were disagreements about the definition, not about the world.

The fix was a human edit in two places at once: the prompt was tightened to
*vendor-published only, a community GitHub server does not count*, and the
labelling rule in `evals/LABELLING.md` was written to match. The baseline was
then **re-run under the tightened spec**, so the pass-1 → pass-2 comparison is
not contaminated by a definition change. `data/pass1_loose_spec.json` keeps the
original run for comparison.

This is the finding I would defend hardest in an interview: a measurable slice of
what looked like model error was specification error. On a real toolkit-research
pipeline, that is the difference between hiring more reviewers and writing a
better field definition.

## Change 2 — retrieval aimed per field, not per app

The naive grounded pass ran one query, "<app> API documentation authentication",
and fetched the top pages. That is a query about auth, so it answered the auth
question and left the other two hard fields to guesswork: a docs page almost
never mentions pricing tiers, and it essentially never mentions MCP.

Three narrow queries replaced one wide one, each aimed at a field the eval showed
was weak:

    "<app> API documentation authentication"          -> primary_auth, api_surface
    "<app> developer API access requirements pricing" -> access
    "<app> MCP server model context protocol"         -> has_mcp

Same credit cost, because Firecrawl bills per *result* and not per query, so
three queries at two results each costs what one at six does. The difference is
that the six results now cover three subjects instead of one.

## Change 3 — a critic that can only demote

The extractor is fluent and will happily write a confident `access` value off a
page that never mentioned access. A second model, from a different family,
re-reads the same corpus and is instructed to refute rather than confirm.
Anything it cannot find support for is demoted to `unknown` and its confidence is
capped.

The critic **cannot raise a value or invent one**, only lower it. That is a
deliberate limit: a critic allowed to correct is just a second extractor with an
extra chance to hallucinate, and its errors would be invisible because they would
look like fixes. Every demotion is recorded in `critic_changes` and shown on the
page.

Cost of the design: the agent ends up knowing less than it might have. That is
the trade — `unknown` is a cheap thing for a human to fix and an expensive thing
to have been wrong about.

## Change 4 — a browser for the one field docs never answer

`access` cannot be read out of API documentation, because API documentation is
written for people who already have an account. The answer lives on the pricing
page, in the shape of the primary call to action.

So a real Chromium opens the pricing page and the developer-portal landing page,
reads the *rendered* text (these are React marketing sites; a plain fetch returns
a shell), classifies the CTA with a fixed rule table, and screenshots it. Only
apps whose `access` was low-confidence or already claimed a gate are probed —
there is no point spending a page load to confirm that Stripe is self-serve.

The classifier is regex, not a model, on purpose. A second LLM reading the same
page is not an independent check on the first one; a rule that fires on the
literal string "Contact sales" is.

## Change 5 — a second oracle, 3× the size of the gold set

20 hand-labelled apps is a small sample, and no amount of care makes it bigger.
But Composio ships production auth configuration for the apps it already covers,
maintained by an entirely different process from this agent. Joining the research
set to that catalog gives a consistency check across every covered app.

It is reported as an **agreement rate**, never folded into the accuracy figure.
Composio's vocabulary is not a vendor's vocabulary — it calls dynamic client
registration `DCR_OAUTH`, for one — so disagreement is evidence to look at, not
automatically an error. The disagreements are listed on the page.

## What did not work

- **Asking for JSON via `response_format`.** The gateway hangs on it outright.
  JSON is enforced by prompt and parsed defensively instead.
- **Trusting the model's citations.** Early runs cited plausible URLs that were
  not in the retrieved corpus. Citations are now intersected against what was
  actually fetched, and anything invented is dropped.
- **One model for everything.** `deepseek-v4-flash` returns an empty completion
  on roughly one call in twelve, consistently for the same input, so retrying it
  is pointless — those calls fall through to a second model rather than dropping
  the app.
- **Retrieval width.** The free Firecrawl tier was projected to run dry at app 55
  of 100. Retrieval was cut from four pages per app to two. Some of the remaining
  errors are bought by that, and the page says so rather than pretending the
  budget was infinite.
