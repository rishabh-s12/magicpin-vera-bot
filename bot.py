"""
bot.py — Vera bot HTTP server for the magicpin AI Challenge.

Implements the 5-endpoint contract from challenge-testing-brief.md:
    GET  /v1/healthz
    GET  /v1/metadata
    POST /v1/context   (scope: category | merchant | customer | trigger)
    POST /v1/tick
    POST /v1/reply

State is in-memory (contexts, per-merchant auto-reply streaks, per-
conversation history/status, and which suppression_keys have already been
sent at which context-version signature). This is fine for a single-process
test run — the brief says "storing in memory is fine; just don't restart
between calls." Swap for Redis/Postgres before using this beyond the
challenge.

Run: uvicorn bot:app --host 0.0.0.0 --port 8080
"""

import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from composer import compose
from conversation import handle_reply, SENT_BODIES, CONVERSATION_STATUS

app = FastAPI(title="Vera Bot — magicpin AI Challenge", version="1.0.0")
START_TIME = time.time()

# ---------------------------------------------------------------------------
# In-memory context store: (scope, context_id) -> {"version": int, "payload": dict}
# ---------------------------------------------------------------------------

CONTEXTS: Dict[Tuple[str, str], Dict[str, Any]] = {}

# suppression_key -> version-signature last used to send, so we don't resend
# identical composition but DO resend when the underlying context changed.
SENT_AT_SIGNATURE: Dict[str, tuple] = {}

MAX_ACTIONS_PER_TICK = 20

TEAM_METADATA = {
    "team_name": "Rishabh Sagar",
    "team_members": ["Rishabh Sagar"],
    "model": "rule-based (no LLM call) — see README for rationale",
    "approach": "Deterministic dispatch by trigger.kind, each handler grounded "
                "directly in category digest/peer_stats + merchant performance/"
                "offers/signals + (when present) customer relationship/preferences. "
                "No generation step invents data outside the pushed contexts.",
    "contact_email": "rishabhsagar_23it132@dtu.ac.in",
    "version": "1.0.0",
    "submitted_at": datetime.utcnow().isoformat() + "Z",
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ContextPayload(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


class TickPayload(BaseModel):
    now: str
    available_triggers: List[str] = []


class ReplyPayload(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: Optional[str] = None
    turn_number: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_ctx(scope: str, context_id: str) -> Optional[dict]:
    entry = CONTEXTS.get((scope, context_id))
    return entry["payload"] if entry else None


def _ctx_version(scope: str, context_id: str) -> int:
    entry = CONTEXTS.get((scope, context_id))
    return entry["version"] if entry else 0


def _counts_loaded() -> Dict[str, int]:
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _cid) in CONTEXTS.keys():
        if scope in counts:
            counts[scope] += 1
    return counts


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/healthz")
def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": _counts_loaded(),
    }


@app.get("/v1/metadata")
def metadata():
    return TEAM_METADATA


@app.post("/v1/context")
def push_context(body: ContextPayload):
    if body.scope not in ("category", "merchant", "customer", "trigger"):
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope", "details": f"unknown scope '{body.scope}'"},
        )

    key = (body.scope, body.context_id)
    current = CONTEXTS.get(key)

    if current and current["version"] == body.version:
        # Idempotent no-op: re-posting the exact same version is accepted,
        # not treated as stale (stale_version is reserved for version < current).
        return {
            "accepted": True,
            "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": datetime.utcnow().isoformat() + "Z",
        }

    if current and current["version"] > body.version:
        return JSONResponse(
            status_code=409,
            content={"accepted": False, "reason": "stale_version", "current_version": current["version"]},
        )

    CONTEXTS[key] = {"version": body.version, "payload": body.payload}
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/v1/tick")
def tick(body: TickPayload):
    actions = []

    for trigger_id in body.available_triggers[:MAX_ACTIONS_PER_TICK * 2]:  # headroom before our own cap
        if len(actions) >= MAX_ACTIONS_PER_TICK:
            break

        trigger = _get_ctx("trigger", trigger_id)
        if not trigger:
            continue  # judge referenced a trigger we were never pushed; skip silently

        merchant_id = trigger.get("merchant_id")
        merchant = _get_ctx("merchant", merchant_id) if merchant_id else None
        if not merchant:
            continue

        category_slug = merchant.get("category_slug")
        category = _get_ctx("category", category_slug) if category_slug else {}

        customer_id = trigger.get("customer_id")
        customer = _get_ctx("customer", customer_id) if customer_id else None

        # Turn economy: skip if we've already sent for this suppression_key
        # at this exact combination of context versions (nothing changed).
        skey = trigger.get("suppression_key") or f"{trigger.get('kind')}:{merchant_id}"
        signature = (
            trigger_id, _ctx_version("trigger", trigger_id),
            _ctx_version("merchant", merchant_id) if merchant_id else 0,
            _ctx_version("category", category_slug) if category_slug else 0,
            _ctx_version("customer", customer_id) if customer_id else 0,
        )
        if SENT_AT_SIGNATURE.get(skey) == signature:
            continue  # restraint: nothing new to say since we last sent this

        result = compose(category or {}, merchant, trigger, customer)

        conversation_id = f"conv_{merchant_id}_{trigger_id}"

        # Anti-repetition guard: never resend an identical body on a conv.
        prior_bodies = SENT_BODIES.setdefault(conversation_id, set())
        if result["body"] in prior_bodies:
            continue
        prior_bodies.add(result["body"])

        SENT_AT_SIGNATURE[skey] = signature

        actions.append({
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": result["send_as"],
            "trigger_id": trigger_id,
            "template_name": f"vera_{trigger.get('kind','generic')}_v1",
            "template_params": [merchant_name_safe(merchant), result["body"]],
            "body": result["body"],
            "cta": result["cta"],
            "suppression_key": skey,
            "rationale": result["rationale"],
        })

    return {"actions": actions}


def merchant_name_safe(merchant: dict) -> str:
    return (merchant.get("identity") or {}).get("name", "there")


@app.post("/v1/reply")
def reply(body: ReplyPayload):
    result = handle_reply(body.conversation_id, body.merchant_id or "", body.message, body.turn_number)

    # Anti-repetition guard applies here too.
    if result.get("action") == "send":
        prior_bodies = SENT_BODIES.setdefault(body.conversation_id, set())
        if result["body"] in prior_bodies:
            result = {
                "action": "wait",
                "wait_seconds": 3600,
                "rationale": "Would have repeated an identical prior message on this conversation; waiting instead.",
            }
        else:
            prior_bodies.add(result["body"])

    return result


@app.post("/v1/teardown")
def teardown():
    """Optional: wipe state at end of test, per privacy §11 of the testing brief."""
    CONTEXTS.clear()
    SENT_AT_SIGNATURE.clear()
    SENT_BODIES.clear()
    CONVERSATION_STATUS.clear()
    return {"status": "wiped"}
