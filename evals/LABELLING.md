# How the gold set was built

20 of the 100 apps, hand-labelled by a human against primary sources before the
grading code was written. Every label in `gold.csv` carries the URL it came from.

## Why these 20

Stratified on purpose, because a random sample of 100 mostly-famous SaaS apps is
a sample of easy apps and would flatter the agent:

| Stratum | Apps | Why |
| --- | --- | --- |
| Household-name, well documented | Slack, Stripe, GitHub, Notion, Linear, Airtable | The agent should be near-perfect here. If it is not, nothing else matters. |
| Mid-market, docs public but less indexed | Klaviyo, Gorgias, Front, Attio, Clay | Where recall gets thin and retrieval starts earning its cost. |
| Gated by approval, plan, or sales | Amazon SP-API, LinkedIn Ads, WhatsApp Business, DealCloud, Gladly, PitchBook | The `access` field is the whole assignment. Docs almost never state the gate. |
| Category errors | Sherlock, Mermaid CLI, Twenty | Not hosted SaaS at all. A CLI has no auth and no API; an agent that pattern-matches "SaaS row" will invent one. |

## The rules the labels follow

Ambiguity in a gold set is indistinguishable from model error, so each field got
a written tie-break rule *before* labelling:

- **primary_auth** — the scheme a third-party, multi-tenant integration would use
  to act on behalf of a customer. Where a vendor documents both a personal API
  key and an OAuth app for third parties, OAuth wins, because that is the shape a
  toolkit ships. Recorded as a single value; predictions that name a real but
  non-primary scheme are counted separately as near-misses rather than being
  quietly forgiven.
- **access** — can an outside developer obtain a *working credential* without a
  human at the vendor. A free trial that issues a real token counts as
  `self_serve_free` even when the product has no free tier. Whether the product
  is expensive is irrelevant; whether a human gates the credential is the test.
- **has_mcp** — **vendor-published only**. Community servers on GitHub do not
  count. This rule was tightened mid-project: see `../docs/ITERATIONS.md`. It is
  the single largest source of label ambiguity in the set, and the agent prompt
  was changed to match the rule rather than the rule being bent to the agent.
- **verdict** — buildability of a *toolkit*, not of a demo. Public docs plus a
  BYO-credential model is `build_with_caveats` even when the labeller cannot get
  a tenant, because Composio's customers bring their own. This distinction is the
  most consequential judgement in the whole exercise and it is argued in
  `../docs/FINDINGS.md`.

## One label was changed after the fact

`verdict` for **Mermaid CLI** was moved from `build_now` to `build_with_caveats`.
Writing the derived-verdict rule in code forced the two command-line entries in
the set to be compared side by side, and they had been labelled differently —
Sherlock as `build_with_caveats` because wrapping a binary is a different build
shape, Mermaid CLI as `build_now` because it felt easy. Both are npm/pip CLIs
with no tenant and no credential. The inconsistency was mine, not the agent's.

Changing a gold label after seeing results is exactly the move that invalidates
an eval, so: it is disclosed here, it is one label out of 100, it was made to
enforce a rule rather than to match any prediction, and the agent's answer for
that cell was wrong both before and after the change.

## Independence

The gold labels were fixed before `pass2.json` was read. Three apps
(Salesforce, HubSpot, Pipedrive) were seen during an early smoke test of the
pipeline and were deliberately **excluded** from the gold set for that reason.

Composio's own catalog is used as a second, larger cross-check (59 apps) but it
is reported separately as an *agreement rate*, never folded into the accuracy
number — the two measurements are not independent enough to average.

## What this sample cannot tell you

20 apps means a single label flip moves accuracy by 5 points, and the 95%
confidence interval on a 90% result is roughly ±13 points. The number is a
direction, not a guarantee, and the report says so.
