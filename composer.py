"""
composer.py — the deterministic decision + copy engine for Vera.
(v2 — synced from user's latest GitHub version, patched here for testing)
"""

from typing import Optional, Dict, Any, List


def g(d: Optional[dict], *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def merchant_name(merchant: dict) -> str:
    return g(merchant, "identity", "name") or "there"


def owner_first(merchant: dict) -> Optional[str]:
    return g(merchant, "identity", "owner_first_name")


def salutation(merchant: dict, category: dict) -> str:
    owner = owner_first(merchant)
    examples = g(category, "voice", "salutation_examples") or []
    if owner and examples and any("{first_name}" in e for e in examples):
        template = next(e for e in examples if "{first_name}" in e)
        return template.replace("{first_name}", owner)
    return owner or merchant_name(merchant)


def is_hindi_mix(merchant: dict, customer: Optional[dict] = None) -> bool:
    if customer:
        pref = (g(customer, "identity", "language_pref") or "").lower()
        return "hi" in pref
    langs = g(merchant, "identity", "languages") or []
    return "hi" in langs


def locality(merchant: dict) -> str:
    return g(merchant, "identity", "locality") or g(merchant, "identity", "city") or "your area"


def active_offers(merchant: dict) -> List[dict]:
    return [o for o in (g(merchant, "offers") or []) if o.get("status") == "active"]


def digest_item(category: dict, item_id: Optional[str]) -> Optional[dict]:
    if not item_id:
        return None
    for item in (g(category, "digest") or []):
        if item.get("id") == item_id:
            return item
    return None


def signals(merchant: dict) -> List[str]:
    return g(merchant, "signals") or []


def has_signal(merchant: dict, prefix: str) -> bool:
    return any(s == prefix or s.startswith(prefix + ":") for s in signals(merchant))


def fmt_pct(x) -> str:
    try:
        return f"{abs(float(x)) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(x)


def suppression_key_for(trigger: dict) -> str:
    return trigger.get("suppression_key") or f"{trigger.get('kind','unknown')}:{trigger.get('merchant_id','')}"


def category_slug(category: dict, merchant: dict) -> str:
    return (g(category, "slug") or merchant.get("category_slug") or "").lower()


def is_category(category: dict, merchant: dict, *keys) -> bool:
    slug = category_slug(category, merchant)
    return any(k in slug for k in keys)


def _h_research_digest(category, merchant, trigger, customer):
    item = digest_item(category, g(trigger, "payload", "top_item_id"))
    name = salutation(merchant, category)
    if not item:
        return (f"{name}, this week's {g(category,'slug',default='category')} research digest has an item "
                f"worth a look. Want me to pull the summary?", "open_ended")
    title = item.get("title", "a new finding")
    source = item.get("source", "")
    trial_n = item.get("trial_n")
    segment = item.get("patient_segment") or item.get("actionable")
    cohort_note = ""
    if segment and any("high_risk" in s for s in signals(merchant)) and "high_risk" in str(segment):
        cohort_note = " — relevant to your high-risk adult cohort"
    n_str = f"{trial_n:,}-{'patient' if trial_n else ''} " if trial_n else ""
    body = (
        f"{name}, {g(category,'slug',default='')} digest just landed. {n_str}study found: {title}{cohort_note}. "
        f"Worth a 2-min look? — {source}"
    )
    return body, "open_ended"


def _h_regulation_change(category, merchant, trigger, customer):
    item = digest_item(category, g(trigger, "payload", "top_item_id"))
    name = salutation(merchant, category)
    deadline = g(trigger, "payload", "deadline_iso")
    if not item:
        return (f"{name}, a regulation update affecting {g(category,'slug',default='your category')} just dropped, "
                f"deadline {deadline or 'soon'}. Want the details?", "binary_yes_no")
    body = (
        f"{name}, compliance heads-up: {item.get('title','a regulation change')} "
        f"({item.get('source','')}). Deadline {deadline or 'not specified'}. "
        f"{item.get('actionable', 'Want me to draft what you need to check?')}"
    )
    return body, "binary_yes_no"


def _h_cde_opportunity(category, merchant, trigger, customer):
    payload = g(trigger, "payload") or {}
    item_id = payload.get("digest_item_id") or payload.get("top_item_id")
    item = digest_item(category, item_id)
    name = salutation(merchant, category)
    credits = payload.get("credits") or (item.get("credits") if item else None)
    fee = payload.get("fee", "").replace("_", " ") if payload.get("fee") else None
    if not item:
        extra = f" ({credits} credits)" if credits else ""
        return (f"{name}, there's a CE/CDE session coming up{extra}. Want the details?", "binary_yes_no")
    date = item.get("date", "")
    credit_str = f", {credits} credits" if credits else ""
    fee_str = f" — {fee}" if fee else ""
    body = (
        f"{name}, {item.get('title','a CE session')} — {date}{credit_str}{fee_str}. "
        f"{item.get('summary','')} Want me to note it in your calendar?"
    )
    return body, "binary_yes_no"


def _h_perf_spike(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    perf = g(merchant, "performance") or {}
    views_pct = g(perf, "delta_7d", "views_pct")
    views = perf.get("views")
    peer_views = g(category, "peer_stats", "avg_views_30d")
    if views_pct:
        cmp_str = ""
        if views is not None and peer_views:
            cmp_str = f" That puts you at {views:,} vs a peer average of {peer_views:,}."
        body = (
            f"{name}, your listing views are up {fmt_pct(views_pct)} this week"
            + (f" ({views:,} total)" if views else "")
            + f".{cmp_str} Good moment to push a post or highlight your top offer while attention is high — want me to draft one?"
        )
    else:
        payload_note = g(trigger, "payload", "note") or "your profile is seeing more activity than usual"
        body = f"{name}, {payload_note}. Want me to capitalize with a fresh post?"
    return body, "binary_yes_no"


def _h_perf_dip(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    perf = g(merchant, "performance") or {}
    calls_pct = g(perf, "delta_7d", "calls_pct")
    offers = active_offers(merchant)
    peer_ctr = g(category, "peer_stats", "avg_ctr")
    ctr = perf.get("ctr")
    drop_str = f"{fmt_pct(calls_pct)} drop in calls this week" if calls_pct else "a dip in activity this week"

    if not offers:
        cmp_str = ""
        if ctr is not None and peer_ctr:
            cmp_str = f" Your CTR is {ctr*100:.1f}% vs a peer average of {peer_ctr*100:.1f}%."
        body = (
            f"{name}, {drop_str}.{cmp_str} You don't have an active offer right now — "
            f"want me to set one up from your catalog to bring people back?"
        )
    else:
        title = offers[0].get("title", "your current offer")
        body = (
            f"{name}, {drop_str} despite \"{title}\" being live — it may not be getting seen. "
            f"Want me to boost it to the top of your listing?"
        )
    return body, "binary_yes_no"


def _customer_noun(category: dict) -> str:
    slug = (g(category, "slug") or "").lower()
    mapping = {
        "restaurant": "diners",
        "dental": "patients",
        "healthcare": "patients",
        "clinic": "patients",
        "pharmacy": "customers",
        "salon": "clients",
        "beauty": "clients",
        "fitness": "members",
        "gym": "members",
        "yoga": "members",
    }
    for key, noun in mapping.items():
        if key in slug:
            return noun
    return "customers"


def _h_milestone_reached(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    payload = g(trigger, "payload") or {}
    metric = payload.get("metric")
    value_now = payload.get("value_now")
    milestone_value = payload.get("milestone_value")
    is_imminent = payload.get("is_imminent")

    # Real structured milestone data (not every milestone_reached trigger
    # in the dataset is a placeholder — some carry an actual metric/value).
    # Use it when present instead of falling back to a generic count, and
    # get the tense right: "is_imminent" + value_now < milestone_value
    # means they haven't crossed it yet.
    if metric and milestone_value is not None:
        metric_label = {"review_count": "reviews"}.get(metric, metric.replace("_", " "))
        if is_imminent and value_now is not None and value_now < milestone_value:
            remaining = milestone_value - value_now
            body = (
                f"{name}, you're just {remaining} {metric_label} away from {milestone_value} {metric_label} — "
                f"want me to line up a post to go out the moment you hit it?"
            )
        else:
            body = (
                f"{name}, you just crossed {milestone_value} {metric_label} 🎉 "
                f"Want me to draft a quick post to share it while it's fresh?"
            )
        return body, "binary_yes_no"

    # Fallback for placeholder-only milestone triggers: use the customer
    # aggregate count. No peer comparison here — none of the category's
    # peer_stats fields actually measure "unique customers this year", so
    # claiming above/below peer average would be an unverifiable guess.
    milestone = payload.get("milestone") or payload.get("note")
    agg = g(merchant, "customer_aggregate") or {}
    noun = _customer_noun(category)
    if not milestone:
        reviews = agg.get("total_unique_ytd")
        milestone = f"{reviews} {noun} this year" if reviews else "a milestone"
    body = f"{name}, you just crossed {milestone} 🎉 Want me to draft a quick post to share it while it's fresh?"
    return body, "binary_yes_no"


def _h_review_theme_emerged(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    theme_from_trigger = g(trigger, "payload", "theme")
    themes = g(merchant, "review_themes") or []
    match = next((t for t in themes if t.get("theme") == theme_from_trigger), None) or (themes[0] if themes else None)
    if not match:
        return (f"{name}, a review pattern is emerging worth a look — want me to pull the details?", "open_ended")
    occ = match.get("occurrences_30d")
    sentiment = match.get("sentiment")
    theme = match.get("theme", "").replace("_", " ")
    if sentiment == "neg":
        body = (
            f"{name}, {occ} reviews this month mention {theme} — worth addressing before it becomes a pattern. "
            f"Want me to draft a response template for it?"
        )
    else:
        body = (
            f"{name}, {occ} reviews this month specifically praise {theme} — want me to pull a quote for your GBP posts?"
        )
    return body, "binary_yes_no"


def _h_competitor_opened(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    payload = g(trigger, "payload") or {}
    distance = payload.get("distance_km")
    comp_name = payload.get("competitor_name")
    their_offer = payload.get("their_offer")
    dist_str = f" {distance}km away" if distance else " nearby"
    if comp_name:
        offer_str = f", leading with \"{their_offer}\"" if their_offer else ""
        body = (
            f"{name}, {comp_name} opened{dist_str} on Google{offer_str}. "
            f"Want me to check how your listing compares side-by-side?"
        )
    else:
        slug = g(category, "slug", default="competitor")
        body = (
            f"{name}, a new {slug} listing opened{dist_str} in {locality(merchant)} on Google. "
            f"Want me to check how your listing compares side-by-side?"
        )
    return body, "open_ended"


def _h_dormant_with_vera(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    days = None
    for s in signals(merchant):
        if s.startswith("stale_posts:"):
            days = s.split(":")[1]
    offers = active_offers(merchant)
    loc = locality(merchant)
    hook = (
        f"your last post was {days} ago" if days else
        (f"\"{offers[0]['title']}\" is still your only live offer" if offers else f"it's been quiet on your {loc} listing")
    )
    body = (
        f"{name}, {hook}. Quick one: what's the treatment/service you're getting asked about most this week? "
        f"I'll turn it into a post."
    )
    return body, "open_ended"


def _h_renewal_due(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    days = g(merchant, "subscription", "days_remaining")
    plan = g(merchant, "subscription", "plan")
    body = (
        f"{name}, your {plan or 'plan'} renews in {days if days is not None else 'a few'} days. "
        f"Want me to lock in the renewal now so there's no gap in your listing visibility?"
    )
    return body, "binary_yes_no"


def _h_gbp_unverified(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    body = (
        f"{name}, your Google Business Profile is still unverified — that's likely capping your visibility. "
        f"Want me to walk you through verification? Takes about 5 minutes."
    )
    return body, "binary_yes_no"


def _h_festival_upcoming(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    payload = g(trigger, "payload") or {}
    festival = payload.get("festival") or payload.get("name")
    days = payload.get("days_until")
    offers = active_offers(merchant)
    if not festival:
        # Placeholder trigger with no real festival name/date — ground in
        # what we do know (locality, existing offer) instead of a bare
        # "the festival is coming up" line.
        loc = locality(merchant)
        if offers:
            body = (
                f"{name}, festival season is coming up for {loc} — want me to build a limited-time "
                f"version of your \"{offers[0]['title']}\" offer around it?"
            )
        else:
            body = f"{name}, festival season is coming up for {loc} — want me to draft a festival offer from your catalog?"
        return body, "binary_yes_no"
    when = f"in {days} days" if days else "coming up"
    offer_note = f" I can tie it to your \"{offers[0]['title']}\" offer." if offers else " Want me to draft a festival offer from your catalog?"
    body = f"{name}, {festival} is {when}.{offer_note}"
    return body, "binary_yes_no"


def _h_curious_ask(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    question = g(trigger, "payload", "question")
    if not question:
        slug = g(category, "slug", default="")
        loc = locality(merchant)
        # Ground the fallback in the merchant's actual category/locality
        # instead of a pure generic line, so it reads as merchant-specific.
        question = f"what's the one {slug or 'service'} question {loc} customers ask you most?"
    body = f"{name}, quick one: {question}"
    return body, "open_ended"


def _h_recall_due(category, merchant, trigger, customer):
    cust_name = g(customer, "identity", "name") or "there"
    m_name = merchant_name(merchant)
    last_visit = g(trigger, "payload", "last_service_date") or g(customer, "relationship", "last_visit")
    slots = g(trigger, "payload", "available_slots") or []
    offers = active_offers(merchant)
    slot_str = " or ".join(s.get("label", "") for s in slots[:2]) if slots else "a convenient slot"
    offer_str = f" {offers[0]['title']}." if offers else ""
    body = (
        f"Hi {cust_name}, {m_name} here. It's time for your recall check-up"
        + (f" (last visit {last_visit})" if last_visit else "")
        + f". We have {slot_str} available.{offer_str} Reply with your preferred slot."
    )
    return body, "multi_choice_slot"


def _h_winback(category, merchant, trigger, customer):
    cust_name = g(customer, "identity", "name") or "there"
    m_name = merchant_name(merchant)
    last_visit = g(customer, "relationship", "last_visit")
    visits = g(customer, "relationship", "visits_total")
    offers = active_offers(merchant)
    offer_str = f" As a thank you, \"{offers[0]['title']}\" is on us this time." if offers else ""
    body = (
        f"Hi {cust_name}, {m_name} here — it's been a while since your last visit"
        + (f" ({last_visit})" if last_visit else "")
        + (f" out of {visits} total visits with us" if visits else "")
        + f".{offer_str} Want to book back in?"
    )
    return body, "binary_yes_no"


def _h_wedding_package_followup(category, merchant, trigger, customer):
    cust_name = g(customer, "identity", "name") or "there"
    m_name = merchant_name(merchant)
    payload = g(trigger, "payload") or {}
    days = payload.get("days_to_wedding")
    next_step = (payload.get("next_step_window_open") or "").replace("_", " ")
    body = (
        f"Hi {cust_name}, {m_name} here — {days} days to go! "
        + (f"Your {next_step} window is now open" if next_step else "Time for your next pre-wedding step")
        + " — want to lock in a slot?"
    )
    return body, "binary_yes_no"


def _h_ipl_match_today(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    payload = g(trigger, "payload") or {}
    match = payload.get("match")
    venue = payload.get("venue")
    when = payload.get("match_time_iso", "")
    time_str = when.split("T")[1][:5] if "T" in when else ""
    body = (
        f"{name}, {match} tonight" + (f" ({time_str})" if time_str else "")
        + (f", {venue} crowd nearby" if venue else "")
        + " — good night for walk-ins. Want me to push a match-night offer to your listing now?"
    )
    return body, "binary_yes_no"


def _h_active_planning_intent(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    payload = g(trigger, "payload") or {}
    topic = (payload.get("intent_topic") or "").replace("_", " ")
    last_msg = payload.get("merchant_last_message")
    if topic:
        body = f"{name}, following up on the {topic} idea"
        if last_msg:
            body += f" — you said \"{last_msg}\""
        body += ". I can have a draft ready in a few minutes — want me to go ahead?"
    else:
        body = f"{name}, ready to move ahead on what we discussed? I can have it live in a few minutes."
    return body, "binary_confirm_cancel"


def _h_trial_followup(category, merchant, trigger, customer):
    cust_name = g(customer, "identity", "name") or "there"
    m_name = merchant_name(merchant)
    payload = g(trigger, "payload") or {}
    trial_date = payload.get("trial_date")
    options = payload.get("next_session_options") or []
    slot_str = " or ".join(o.get("label", "") for o in options[:2]) if options else "our next session"
    body = (
        f"Hi {cust_name}, {m_name} here — hope the trial"
        + (f" on {trial_date}" if trial_date else "")
        + f" went well! Next session slot: {slot_str}. Want to continue?"
    )
    return body, "binary_yes_no"


def _h_supply_alert(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    payload = g(trigger, "payload") or {}
    molecule = payload.get("molecule")
    batches = payload.get("affected_batches") or []
    manufacturer = payload.get("manufacturer")
    batch_str = ", ".join(batches) if batches else "affected batches"
    body = (
        f"{name}, supply alert: {molecule or 'a stocked medicine'} recall"
        + (f" from {manufacturer}" if manufacturer else "")
        + f" — batch(es) {batch_str}. Want me to help you flag these in your shelf stock?"
    )
    return body, "binary_yes_no"


def _h_chronic_refill_due(category, merchant, trigger, customer):
    """Customer-facing recurring-consumption reminder. This kind gets
    assigned to merchants across ALL categories in the dataset (not just
    pharmacies), so branch on category instead of assuming medicine."""
    cust_name = g(customer, "identity", "name") or "there"
    m_name = merchant_name(merchant)
    payload = g(trigger, "payload") or {}

    if is_category(category, merchant, "pharma", "chemist"):
        runs_out = payload.get("stock_runs_out_iso", "")
        runs_out_date = runs_out.split("T")[0] if runs_out else "soon"
        delivery = payload.get("delivery_address_saved")
        delivery_note = " We can deliver to your saved address." if delivery else ""
        body = (
            f"Hi {cust_name}, {m_name} here — your regular refill is running low, expected to last until "
            f"{runs_out_date}. Want us to prepare your usual refill for pickup or delivery?{delivery_note}"
        )
    else:
        # Generic recurring-visit framing for non-pharmacy merchants this
        # kind lands on (dental, salon, fitness, etc.) instead of medical
        # "refill" language that doesn't fit those categories.
        last_visit = g(customer, "relationship", "last_visit")
        visit_note = f" since your last visit ({last_visit})" if last_visit else " for a while"
        body = (
            f"Hi {cust_name}, {m_name} here — it's about time for your next regular top-up{visit_note}. "
            f"Want me to book your usual slot?"
        )
    return body, "binary_yes_no"


def _h_appointment_tomorrow(category, merchant, trigger, customer):
    cust_name = g(customer, "identity", "name") or "there"
    m_name = merchant_name(merchant)
    payload = g(trigger, "payload") or {}
    when = payload.get("appointment_time") or payload.get("slot") or "tomorrow"
    services = g(customer, "relationship", "services_received") or []
    last_service = services[-1].replace("_", " ") if services else None
    service_note = f" for your {last_service}" if last_service else ""
    body = (
        f"Hi {cust_name}, {m_name} here — reminder that you're booked in {when}"
        f"{service_note}. Reply to confirm, or let us know if you need to reschedule."
    )
    return body, "binary_yes_no"


def _h_category_seasonal(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    payload = g(trigger, "payload") or {}
    season = (payload.get("season") or "").replace("_", " ")
    trends = payload.get("trends") or []
    top_trend = trends[0].replace("_", " ") if trends else None
    body = (
        f"{name}, {season} shift incoming"
        + (f" — {top_trend} leading the change" if top_trend else "")
        + ". Want me to suggest shelf/listing adjustments based on it?"
    )
    return body, "binary_yes_no"


def _h_generic(category, merchant, trigger, customer):
    name = salutation(merchant, category)
    kind = trigger.get("kind", "an update").replace("_", " ")
    payload = g(trigger, "payload") or {}
    fact = None
    for k, v in payload.items():
        if isinstance(v, bool) or k in ("category", "placeholder"):
            continue
        if isinstance(v, (str, int, float)):
            fact = f"{k.replace('_',' ')}: {v}"
            break
    if customer:
        cust_name = g(customer, "identity", "name") or "there"
        body = (
            f"Hi {cust_name}, {merchant_name(merchant)} here — {kind}"
            + (f" ({fact})" if fact else "")
            + ". Want to know more?"
        )
        return body, "open_ended"
    body = f"{name}, heads up on {kind}" + (f" — {fact}" if fact else "") + ". Want the details?"
    return body, "open_ended"


HANDLERS = {
    "research_digest": _h_research_digest,
    "regulation_change": _h_regulation_change,
    "cde_opportunity": _h_cde_opportunity,
    "perf_spike": _h_perf_spike,
    "perf_dip": _h_perf_dip,
    "seasonal_perf_dip": _h_perf_dip,
    "milestone_reached": _h_milestone_reached,
    "review_theme_emerged": _h_review_theme_emerged,
    "competitor_opened": _h_competitor_opened,
    "dormant_with_vera": _h_dormant_with_vera,
    "renewal_due": _h_renewal_due,
    "gbp_unverified": _h_gbp_unverified,
    "festival_upcoming": _h_festival_upcoming,
    "curious_ask_due": _h_curious_ask,
    "scheduled_recurring": _h_curious_ask,
    "active_planning_intent": _h_active_planning_intent,
    "recall_due": _h_recall_due,
    "winback_eligible": _h_winback,
    "customer_lapsed_soft": _h_winback,
    "customer_lapsed_hard": _h_winback,
    "trial_followup": _h_trial_followup,
    "wedding_package_followup": _h_wedding_package_followup,
    "ipl_match_today": _h_ipl_match_today,
    "supply_alert": _h_supply_alert,
    "chronic_refill_due": _h_chronic_refill_due,
    "category_seasonal": _h_category_seasonal,
    "appointment_tomorrow": _h_appointment_tomorrow,
}


def strip_urls_penalty_guard(body: str) -> str:
    import re
    return re.sub(r"https?://\S+", "", body).strip()


def compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> Dict[str, Any]:
    category = category or {}
    merchant = merchant or {}
    trigger = trigger or {}

    kind = trigger.get("kind", "")
    scope = trigger.get("scope", "merchant")
    handler = HANDLERS.get(kind, _h_generic)

    body, cta = handler(category, merchant, trigger, customer)
    body = strip_urls_penalty_guard(body)

    send_as = "merchant_on_behalf" if (scope == "customer" or customer is not None) else "vera"
    skey = suppression_key_for(trigger)

    rationale = (
        f"trigger.kind={kind} (scope={scope}, urgency={trigger.get('urgency','?')}); "
        f"handler={'named:' + kind if kind in HANDLERS else 'generic-fallback'}; "
        f"grounded in {'customer + ' if customer else ''}merchant signals/performance/offers "
        f"and category digest/peer_stats where available."
    )

    return {
        "body": body,
        "cta": cta,
        "send_as": send_as,
        "suppression_key": skey,
        "rationale": rationale,
    }
