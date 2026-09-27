# Vera Bot — magicpin AI Challenge

**Team:** Rishabh Sagar
**Contact:** rishabhsagar_23it132@dtu.ac.in
**Live URL:** https://magicpin-vera-bot-edun.onrender.com

## Approach

Deterministic, rule-based dispatch by `trigger.kind` — no LLM call in the
composition path. Each handler is grounded directly in the context the
judge pushes us (category digest/peer_stats, merchant performance/offers/
signals, and customer relationship/preferences where present). Nothing is
ever invented outside those fields.

### Why rule-based instead of an LLM call
- **Zero timeout risk** — no external API round-trip inside the request path.
- **Zero hallucination risk** — every fact in every message is read directly
  off the pushed context dicts.
- **100% reproducibility** — same input always produces the same output,
  which the brief requires.
- **Zero marginal API cost** per action.

Trade-off: less phrasing variety than an LLM-generated message would have.

## Endpoints

- `GET /v1/healthz` — status + uptime + loaded context counts
- `GET /v1/metadata` — team info
- `POST /v1/context` — push category / merchant / customer / trigger context
  (versioned; idempotent no-op on identical version repost, `409` on stale)
- `POST /v1/tick` — evaluate available triggers, return actions (bounded to
  20/tick), suppressing anything already sent for an unchanged context
  signature and never resending an identical body on a conversation
- `POST /v1/reply` — turn-by-turn conversation logic: auto-reply detection
  and backoff (merchant-keyed, not conversation-keyed), explicit opt-out
  handling, hostile-but-not-explicit-stop handling, intent-commit
  fast-forwarding, off-topic redirects, generic engaged-reply fallback
- `POST /v1/teardown` — wipes in-memory state (optional, per privacy §11)

## State

Everything is in-memory (single process). Fine for a single test run;
would move to Redis/Postgres for anything beyond the challenge.

## Coverage

Named handlers for: research_digest, regulation_change, cde_opportunity,
perf_spike, perf_dip / seasonal_perf_dip, milestone_reached,
review_theme_emerged, competitor_opened, dormant_with_vera, renewal_due,
gbp_unverified, festival_upcoming, curious_ask_due / scheduled_recurring,
active_planning_intent, recall_due, winback_eligible /
customer_lapsed_soft / customer_lapsed_hard, trial_followup,
wedding_package_followup, ipl_match_today, supply_alert,
chronic_refill_due, category_seasonal, appointment_tomorrow.

Every other trigger kind falls through to a generic handler that still
grounds itself in real payload/signal fields rather than emitting filler.

## Run locally
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080


## Deployment
Render (free tier). A cron job (cron-job.org) pings `/v1/healthz` every
2 minutes to prevent the free-tier dyno from spinning down before/during
grading.
