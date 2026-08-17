# Toolkit Radar

An agent that researches whether a SaaS app can become an agent toolkit today,
run across 100 apps, and then graded on whether it was telling the truth.

Built for the Composio AI Product Ops take-home.

- **The report** → <https://toolkit-radar-seven.vercel.app>
- **The report, for agents** → `https://toolkit-radar-seven.vercel.app/api/mcp`
  ```
  claude mcp add --transport http toolkit-radar https://toolkit-radar-seven.vercel.app/api/mcp
  ```
- **The raw data** → [`/data.json`](https://toolkit-radar-seven.vercel.app/data.json) ·
  [`/findings.csv`](https://toolkit-radar-seven.vercel.app/findings.csv) ·
  [`/llms.txt`](https://toolkit-radar-seven.vercel.app/llms.txt)

---

## What it answers, per app

| Field | Values |
| --- | --- |
| `primary_auth` | the scheme a third-party integration would use to act for a customer |
| `access` | `self_serve_free` · `self_serve_paid` · `plan_gated` · `approval_required` · `partner_gated` · `no_public_api` |
| `api_surface` / `api_breadth` | `rest` · `graphql` · `rest+graphql` · `soap` · `sdk_only` · `rpc` · `none`, and how much of the product it covers |
| `has_mcp` | does the **vendor** publish an MCP server (community servers do not count) |
| `verdict` | `build_now` · `build_with_caveats` · `needs_outreach` · `not_buildable` |
| `evidence` | URLs that were actually fetched, not URLs the model remembered |
| `confidence` | the agent's own 0–1 per field, and the page checks whether it means anything |

On top of that, three things the brief did not ask for and that turn the table
into a decision:

1. **A gap analysis against Composio's live catalog** — all toolkits pulled
   through the v3 API and joined to the 100, so the output is "here is what you
   don't have and what it costs to add", not "here are 100 rows".
2. **A ranked build queue** with a transparent, hand-weighted score, split into
   lanes: build now / quick win / needs outreach / park.
3. **An MCP server over the findings**, so the deliverable is itself a tool.

---

## Running it

```bash
uv venv && uv pip install -e .          # httpx only; playwright is optional
cp .env.example .env                    # then fill in the three keys

python -m agent.catalog                 # pull + join Composio's toolkit catalog
python -m agent.research both           # pass 1 (control) and pass 2 (grounded)
python -m agent.arbitrate               # pass 3b, merge prior and evidence
python -m agent.gate                    # pass 4, browser gate prover
python -m agent.reach                   # pass 5, touch each documented API
python evals/score.py                   # grade both passes against the gold set
python -m agent.report                  # build site/index.html + data.json
```

Smaller slices while developing:

```bash
python -m agent.research pass2 5        # first 5 apps only
python -m agent.gate 8                  # probe 8 apps with the browser
python peek.py url https://docs.example.com/auth   # what a human uses to check a label
```

Every Firecrawl call is cached to `research_cache/` by content hash, so a re-run
costs no credits and the pipeline is reproducible without draining an account.

### Keys

```
OPENCODE_GO_API_KEY=   # OpenAI-compatible gateway (opencode.ai/zen/go/v1)
COMPOSIO_API_KEY=      # free at composio.dev — used read-only for the catalog
FIRECRAWL_API_KEY=     # search + JS-rendered scrape; free tier is enough
```

`FIRECRAWL_BUDGET` caps billable calls (default 900) so a loop cannot drain the
account.

---

## The pipeline

```
  pass 0   catalog join     1,222 Composio toolkits  ->  matched to the 100
                            fuzzy matches surfaced for a human, never auto-accepted

  pass 1   closed book      one model call, no tools           << the CONTROL
                            exists so the verified number has a baseline

  pass 2   grounded         3 narrow searches -> 2 rendered pages -> extract
                            citations intersected with what was actually fetched

  pass 3   critic           a different model family, told to refute
                            can only demote a claim to unknown, never invent one

  pass 3b  arbitration      evidence updates the prior, silence keeps it, and the
                            verdict is derived so it cannot contradict its own row

  pass 4   gate prover      real Chromium on the pricing page, rule-based CTA
                            classification + screenshot. Not another LLM.

  pass 5   reachability     one unauthenticated GET to each documented API:
                            does the endpoint exist and does it demand a key

  ------   grading          20 hand-labelled apps / 100 labels
                            + agreement against Composio's shipped auth config
```

Two models, deliberately from different families: `deepseek-v4-flash` extracts,
`kimi-k2.6` critiques. A critic from the same family mostly agrees with itself.

---

## Accuracy

    pass 1  closed book          68%    one model call, no tools — the control
    pass 2  grounded             74%    after the extraction prompt was fixed
    pass 2  grounded + critic    42%    the critic, as originally designed
    pass 3  arbitrated           74%    shipped

**+6 points**, and the interesting part is the 42% in the middle. The first
grounded run also scored *below* the control, at 56%. Both regressions came from
instructions that sound obviously right — "do not fall back on what you remember"
and "demote anything you cannot support" — and both destroy information when the
corpus is three pages wide instead of complete. The adversarial critic lost 32
points and now flags rather than decides.

The full account is in `docs/ITERATIONS.md`, including the two roles the critic
failed at (27% and 31% hit rates) and the four merge policies that made no
difference at all.

The method:

- 20 apps hand-labelled from primary sources **before** the grading code existed,
  stratified to include the gated and the weird, because a random sample of
  famous SaaS apps flatters the agent (`evals/LABELLING.md`).
- Pass 1 is a real control run, not an estimate.
- Errors are typed — `abstained` (said unknown), `overclaimed` (invented an
  answer where none is documented), `wrong` — because those are three different
  problems.
- A calibration check asks whether the agent's stated confidence predicts
  correctness at all.
- A second, larger cross-check against Composio's production auth config —
  76% agreement across all 59 apps it already ships, reported as agreement and
  never averaged into the accuracy number.
- Two loops that produce a different *kind* of evidence than a model reading a
  page: a real browser on 45 pricing pages, and one unauthenticated GET to each
  documented API — 43 of which answered 401/403, which is the strongest
  confirmation available that the API and its auth story are real.

What moved the number, and what didn't, is logged in `docs/ITERATIONS.md`.
The most useful finding there: a measurable share of what looked like model error
was **specification** error — `has_mcp` was the worst field because "well-known
third party" is not a decidable definition, not because the model was weak.

---

## Layout

```
agent/
  gateway.py   LLM client — HTTP-200-shaped errors, no response_format, thinking off
  search.py    Firecrawl search + scrape, disk-cached, budget-capped
  schema.py    the closed vocabularies, and the coercion that makes output gradeable
  research.py  passes 1-3
  arbitrate.py pass 3b, the merge rules, each one recorded per field
  gate.py      pass 4, the browser
  reach.py     pass 5, the unauthenticated probe
  catalog.py   Composio catalog pull + conservative join
  score.py     build score, effort tier, lane assignment
  report.py    merges everything into site/data.json and renders index.html
evals/
  gold.csv       20 apps x 5 fields, each with the URL it came from
  LABELLING.md   how the labels were decided, and the tie-break rules
  score.py       the grader
api/mcp.js       MCP server over the findings (JSON-RPC, streamable HTTP, no deps)
site/            template.html -> index.html, plus data.json / findings.csv / llms.txt
docs/            ITERATIONS.md (how accuracy moved), FINDINGS.md (the argument)
```

---

## Honest limits

- 20 apps is a small gold set. One flipped label moves accuracy by a point.
- The free Firecrawl tier bills per search *result*; a full-width first run was
  projected to run dry at app 55, so retrieval was cut to six results and two
  fetched pages per app. Some of the remaining errors are bought by that.
- The critic can only demote, so the agent ends honest rather than complete.
- Private-beta fintech and research-data products (PitchBook, iPayX, Paygent,
  fanbasis, Waterfall) publish marketing pages and nothing else. For those,
  "there is no public evidence" is the finding, and the rows say so.
- No accounts were created anywhere. The browser pass only reads public pages.
