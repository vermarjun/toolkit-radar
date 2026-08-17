# The argument

The report page carries the numbers, and they are regenerated from `data/` on
every build, so this document deliberately avoids repeating figures that can
drift. What it holds is the reasoning — the four claims I would defend in an
interview, and the one I am least sure of.

## 1. "Can we build this?" and "can we get a key?" are different questions

This is the load-bearing claim, and it changes how a fair chunk of the 100 should
be triaged.

Composio's model is bring-your-own-credential: the customer already has a Gladly
tenant, a DealCloud instance, a Zendesk. So when a vendor publishes complete API
documentation but will not sell an outside developer an account, the toolkit is
still writable — every endpoint, every field, every auth header is public. What
the vendor is withholding is a **sandbox to test against**, not the interface.

That reframes a large slice of the "blocked" pile from a partnerships problem
into a QA problem, and QA problems are solvable by a single design partner
willing to point a staging tenant at you. It is a much cheaper unblock than a
commercial negotiation, and it is invisible if you only record "gated: yes".

The counter-case is real and I have kept it separate: sometimes the gate is on
the *integrator*, not the tenant. LinkedIn approves your application, not your
customer's. Meta reviews your app. Amazon registers your developer profile. No
customer can grant you those, and no amount of engineering routes around them.

**Which means the field this schema is missing is: who is the gate on?** Vendor
approves *you* → partnerships. Customer's admin issues the token → nothing to
negotiate, just find a design partner. Those two look identical in every column
this project records, and they demand opposite responses. If I extended this
research tomorrow, that is the field I would add first, ahead of anything else.

## 2. The auth mix of a request list is not the auth mix of a catalog

The 100 apps here skew heavily toward OAuth2. Composio's own catalog of 1,222
toolkits skews just as heavily toward API keys. Both are true, and neither
generalises to the other.

The explanation is selection: a list assembled from *customer requests plus
well-known names* is a list of large, enterprise-sold products, and enterprise
products use OAuth because they have admin consent models and per-seat licensing
to enforce. The long tail of a real catalog is full of small tools that ship a
key in a settings page because that is all they need.

The practical consequence is a planning trap. Sizing OAuth engineering capacity
off a wishlist will overshoot, because the wishlist is the least representative
sample of the catalog you could have drawn.

## 3. MCP is no longer a differentiator, it is a shortcut

A large share of the 100 already publish their own MCP server — including apps
that will not give an outside developer a credential at all. Vendors are shipping
agent surfaces faster than they are opening access.

Two consequences, pointing in opposite directions:

- **It is cheaper to build for those apps**, because the vendor has already done
  the hard product work of deciding which operations are safe to expose to an
  agent. That is a curated tool list, free.
- **It is a competitive clock.** Every vendor MCP is one more reason a customer
  might skip an aggregation layer for that specific app. The value moves toward
  the things a single-vendor MCP cannot do: cross-app workflows, one auth model,
  one audit trail.

Worth noting for calibration: this was the field a closed-book model was worst
at, by a distance. Almost every one of these servers shipped in 2025 or 2026, so
the model is not wrong so much as out of date — which is precisely the class of
question that justifies retrieval existing at all.

## 4. Openness is a property of the category, not the company

The per-category spread is wide and it is not random. Developer tools and
productivity apps sell *to the person doing the integration*, so the credential
is part of the product and the signup is self-serve by design. Enterprise sales
categories treat API access as a contract term, because the API is how you get
locked in and they know it.

Company size predicts this badly; category predicts it well. Which means outreach
can be planned per-category — one conversation pattern for an entire vertical —
rather than app by app.

## The claim I am least sure of

The buildability verdict is derived from `access` and `api_surface` by a fixed
rule (`agent/arbitrate.py:derive_verdict`). That is defensible — a verdict that
contradicts the two fields beside it is worse than no verdict — but it means the
verdict inherits every error in those two fields, and it cannot see the
integrator-vs-tenant distinction from claim 1. LinkedIn Ads is the case where
this visibly breaks: the rule calls it buildable-with-caveats because the docs
are public, and a human calls it outreach because LinkedIn has to approve the
application itself.

I would rather ship a rule that is wrong in a stateable way than a model output
that is wrong in an unstateable one, but it is the weakest link on the page and
the report says so.

## What this does not cover

- Rate limits, pricing per call, and data-residency constraints. All three change
  whether a toolkit is *viable* rather than *possible*, and none are recorded.
- Write access. Several APIs here are read-rich and write-poor, which matters
  enormously for agent use and is invisible in a single `api_breadth` value.
- Anything behind a login. Front's developer portal, for one, requires an account
  to read the docs at all — the finding for that app came from the alternate
  public host, and the row notes it.
