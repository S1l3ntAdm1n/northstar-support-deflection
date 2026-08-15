"""
chatbot.py — Northstar Assistant core logic.

Stateful, session-aware engine that implements the full system prompt spec:
  - Order tracking with proactive delay/exception surfacing
  - Stock checks with plain-language levels and clarifying questions
  - Auto-escalation on 2 failures, frustration keywords, or out-of-scope requests
  - Guardrails: never invents data; redirects off-topic; no sensitive info requests
"""

import os
import re
import sqlite3
from datetime import date


# ---------------------------------------------------------------------------
# DB HELPERS
# ---------------------------------------------------------------------------

def _db_path():
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "database.db")


def _fetch_order(order_id: str):
    """Return (product, status, eta, tracking_update) or None."""
    try:
        conn = sqlite3.connect(_db_path())
        cur = conn.cursor()
        cur.execute(
            "SELECT product, status, eta, tracking_update FROM orders WHERE id = ?;",
            (order_id,),
        )
        row = cur.fetchone()
        conn.close()
        return row
    except Exception:
        return None


def _fetch_stock(product_name: str):
    """Return (sizes, colors, stock_status, quantity, restock_date) or None."""
    try:
        conn = sqlite3.connect(_db_path())
        cur = conn.cursor()
        cur.execute(
            "SELECT sizes, colors, stock_status, quantity, restock_date "
            "FROM inventory WHERE product_name = ?;",
            (product_name.lower().strip(),),
        )
        row = cur.fetchone()
        conn.close()
        return row
    except Exception:
        return None


def _all_product_names():
    """Return list of all product_name strings from inventory."""
    try:
        conn = sqlite3.connect(_db_path())
        cur = conn.cursor()
        cur.execute("SELECT product_name FROM inventory;")
        names = [r[0] for r in cur.fetchall()]
        conn.close()
        return names
    except Exception:
        return []


# ---------------------------------------------------------------------------
# STOCK LEVEL LANGUAGE
# ---------------------------------------------------------------------------

def _stock_level_text(quantity: int) -> str:
    """Map an integer quantity to a plain-language stock description."""
    if quantity <= 0:
        return "out of stock"
    elif quantity <= 3:
        return f"only {quantity} left"
    elif quantity <= 20:
        return "a few left"
    else:
        return "plenty in stock"


# ---------------------------------------------------------------------------
# INTENT DETECTION
# ---------------------------------------------------------------------------

# Keywords that trigger immediate escalation regardless of context
_ESCALATE_IMMEDIATELY = [
    "refund", "return", "damaged", "wrong item", "wrong order",
    "fraud", "chargeback", "complaint", "legal", "sue", "lawyer",
    "attorney", "scam", "stolen",
]

# Frustration signals → escalate
_FRUSTRATION_KEYWORDS = [
    "ridiculous", "unacceptable", "furious", "disgusting", "outraged",
    "terrible", "horrible", "awful", "worst", "useless", "incompetent",
    "i'm done", "never again", "so angry", "so frustrated",
    "really frustrated", "very frustrated", "extremely frustrated",
]

# Human-request signals → escalate
_HUMAN_KEYWORDS = [
    "human", "agent", "representative", "real person", "talk to someone",
    "speak to someone", "talk to a person", "speak to a person",
    "contact support", "customer service", "escalate",
]

# Order tracking signals
_ORDER_KEYWORDS = [
    "order", "shipped", "shipping", "where is", "where's", "tracking",
    "when will", "package", "delivery", "deliver", "parcel", "status",
    "dispatch",
]

# Stock availability signals
_STOCK_KEYWORDS = [
    "stock", "available", "availability", "size", "sizes", "color", "colour",
    "restock", "in stock", "out of stock", "do you have", "do you carry",
    "is there", "have the",
]


def detect_intent(text: str) -> str:
    """
    Returns one of:
      'escalate_immediate' — refund/return/damage/fraud/legal/frustration/human request
      'order_status'
      'stock'
      'unknown'
    """
    if text is None:
        return "unknown"
    t = str(text).lower()

    for kw in _ESCALATE_IMMEDIATELY:
        if kw in t:
            return "escalate_immediate"

    for kw in _FRUSTRATION_KEYWORDS:
        if kw in t:
            return "escalate_immediate"

    for kw in _HUMAN_KEYWORDS:
        if kw in t:
            return "escalate_immediate"

    for kw in _ORDER_KEYWORDS:
        if kw in t:
            return "order_status"

    for kw in _STOCK_KEYWORDS:
        if kw in t:
            return "stock"

    return "unknown"


# ---------------------------------------------------------------------------
# EXTRACTION HELPERS
# ---------------------------------------------------------------------------

def _extract_order_id(text: str):
    """Pull an order number from free text. Returns the digit string or None.
    Accepts any digit count (1, 2, 3+ digits). Priority:
      1. 'order #5', 'order 5', 'order5'
      2. '#5'
      3. Any standalone digit sequence (bare number, e.g. reply to a prompt)
    """
    t = str(text).lower()
    # 1. "order" prefix — most reliable
    order_idx = t.find('order')
    if order_idx != -1:
        remainder = t[order_idx + len('order'):].lstrip()
        if remainder.startswith('#'):
            remainder = remainder[1:].lstrip()

        digits = []
        for ch in remainder:
            if ch.isdigit():
                digits.append(ch)
            else:
                break

        if digits:
            return ''.join(digits)
    # 2. Hash prefix without "order"
    m = re.search(r'#\s*(\d+)', t)
    if m:
        return m.group(1)
    # 3. Bare digit sequence (word-boundary anchored to avoid partial matches)
    m = re.search(r'\b(\d+)\b', t)
    return m.group(1) if m else None


# Words to ignore when doing partial word matching
_STOP_WORDS = {
    'is', 'are', 'the', 'a', 'an', 'in', 'of', 'do', 'you', 'have', 'any',
    'check', 'stock', 'available', 'size', 'sizes', 'color', 'colour', 'what',
    'about', 'for', 'me', 'i', 'want', 'need', 'it', 'some', 'get', 'there',
    'can', 'could', 'would', 'has', 'had', 'my', 'your', 'we', 'they', 'our',
    'please', 'just', 'also', 'and', 'or', 'with', 'from', 'that', 'this',
    # Generic/vague words that are not product names
    'anything', 'something', 'everything', 'nothing', 'stuff', 'things',
    'items', 'products', 'one', 'ones', 'more', 'other', 'another', 'many',
}


def _extract_product_name(text: str, known_products: list) -> tuple:
    """
    Try to match known product names from user text.
    Returns (matched_name_or_None, candidates_list).

    Strategy:
      1. Exact substring match — wins immediately (e.g. 'blue sneakers' in text).
      2. Word-level partial match — splits both query and product names into words,
         finds products that share a meaningful word with the query.
         If exactly one product matches → return it.
         If multiple match → return (None, candidates) so the caller can ask.
    """
    t = text.lower().strip()

    # 1. Exact substring match
    for name in known_products:
        if name in t:
            return name, []

    # 2. Word-level partial match
    query_words = set(re.sub(r'[^\w\s]', '', t).split()) - _STOP_WORDS
    if not query_words:
        return None, []

    candidates = []
    for name in known_products:
        name_words = set(name.split())
        if query_words & name_words:          # any word in common?
            candidates.append(name)

    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates                   # 0 = no match, 2+ = ambiguous


# ---------------------------------------------------------------------------
# RESPONSE HANDLERS
# ---------------------------------------------------------------------------

def _handle_escalation(reason: str = "general") -> dict:
    """
    Returns a response payload that tells the user exactly what happens next
    and triggers the ticket form on the frontend.
    """
    messages = {
        "general": (
            "I'm connecting you with a support agent now. "
            "They'll follow up by email — typical response time is under 10 minutes. "
            "Please fill out the form below so they have the details."
        ),
        "out_of_scope": (
            "I can help with order tracking and stock checks — for anything else, "
            "I can connect you with a human agent. "
            "Fill out the form below and someone will get back to you within 10 minutes."
        ),
        "too_many_failures": (
            "I wasn't able to resolve this automatically. "
            "I'm connecting you with a support agent — they'll follow up by email within 10 minutes. "
            "Please fill out the form below."
        ),
    }
    return {
        "response": messages.get(reason, messages["general"]),
        "show_ticket_form": True,
        "escalated": True,
        "suggest_ticket": False,
        "prefilled_order_id": "",
    }


def _handle_order_status(order_id: str | None, session: dict) -> dict:
    """
    Look up order and return a human-friendly status summary.
    Proactively flags delays and exceptions.
    Tracks failed attempts and auto-escalates after 3.
    """
    today = date.today()

    # No order ID provided — ask for it
    if not order_id:
        session["pending_context"] = "awaiting_order_number"
        return {
            "response": "Sure — what's your order number? You can find it in your confirmation email.",
            "show_ticket_form": False,
            "escalated": False,
            "suggest_ticket": False,
            "prefilled_order_id": "",
        }

    row = _fetch_order(order_id)

    if row is None:
        # Order not found — track attempts
        attempts = session.get("order_lookup_attempts", 0) + 1
        session["order_lookup_attempts"] = attempts

        if attempts >= 3:
            # Auto-escalate after 2 failures
            result = _handle_escalation("too_many_failures")
            result["prefilled_order_id"] = order_id
            return result

        # First failure — ask to double check, offer alternatives
        session["pending_context"] = "awaiting_order_number_retry"
        return {
            "response": (
                f"I couldn't find order #{order_id} — can you double-check the number? "
                "You can also share your email address and I can connect you with an agent who can look it up."
            ),
            "show_ticket_form": False,
            "escalated": False,
            "suggest_ticket": True,
            "prefilled_order_id": order_id,
        }

    # Order found — reset failure counter
    session["order_lookup_attempts"] = 0
    session.pop("pending_context", None)

    product, status, eta, tracking_update = row

    # Build ETA string
    eta_text = ""
    eta_is_past = False
    if eta:
        try:
            eta_date = date.fromisoformat(eta)
            eta_is_past = eta_date < today
            eta_text = eta_date.strftime("%B %d")
        except ValueError:
            eta_text = eta

    # Detect problem statuses
    is_delayed = status in ("delayed",) or eta_is_past and status not in ("delivered",)
    is_exception = status == "delivery_exception"

    # Compose response
    if is_exception:
        tracking_note = f" Last update: {tracking_update}" if tracking_update else ""
        response = (
            f"Heads up — there's a delivery exception on your {product} order #{order_id}.{tracking_note} "
            "You'll want to contact the carrier or let me connect you with our support team."
        )
    elif is_delayed:
        tracking_note = f" Last update: {tracking_update}" if tracking_update else ""
        response = (
            f"Looks like your {product} (order #{order_id}) hit a delay — "
            f"it was expected by {eta_text} but hasn't arrived yet.{tracking_note}"
        )
    elif status == "delivered":
        response = f"Your {product} (order #{order_id}) was delivered on {eta_text}."
    elif status == "shipped":
        eta_part = f", expected by {eta_text}" if eta_text else ""
        tracking_note = f" Last update: {tracking_update}" if tracking_update else ""
        response = f"Your {product} (order #{order_id}) is on its way{eta_part}.{tracking_note}"
    elif status == "processing":
        response = f"Your {product} (order #{order_id}) is still being prepared — it hasn't shipped yet."
    else:
        response = f"Your {product} (order #{order_id}) status: {status}."
        if eta_text:
            response += f" Estimated delivery: {eta_text}."

    return {
        "response": response,
        "show_ticket_form": False,
        "escalated": False,
        "suggest_ticket": is_exception,  # exception orders nudge toward ticket
        "prefilled_order_id": order_id if is_exception else "",
    }


def _handle_stock(product_name: str | None, session: dict) -> dict:
    """
    Check stock for a product.
    Uses plain-language level descriptions.
    Asks a clarifying question if the product can't be identified.
    If multiple products partially match, asks which one the user means.
    Auto-escalates after 2 lookup failures.
    """
    if not product_name:
        # If attempts already exhausted, escalate rather than loop the question
        if session.get("stock_lookup_attempts", 0) >= 3:
            return _handle_escalation("too_many_failures")

        candidates = session.get("product_candidates", [])
        if candidates:
            # We found multiple partial matches — ask which one
            names = " or ".join(f'"{c.title()}"' for c in candidates)
            session["pending_context"] = "awaiting_product_name"
            return {
                "response": f"I found a few options that could match: {names}. Which one did you mean?",
                "show_ticket_form": False,
                "escalated": False,
                "suggest_ticket": False,
                "prefilled_order_id": "",
            }

        # No candidates at all — generic clarifying question
        session["pending_context"] = "awaiting_product_name"
        return {
            "response": "Which item would you like to check? Just give me the product name (and size or color if you have one in mind).",
            "show_ticket_form": False,
            "escalated": False,
            "suggest_ticket": False,
            "prefilled_order_id": "",
        }

    row = _fetch_stock(product_name)

    if row is None:
        attempts = session.get("stock_lookup_attempts", 0) + 1
        session["stock_lookup_attempts"] = attempts

        if attempts >= 3:
            return _handle_escalation("too_many_failures")

        session["pending_context"] = "awaiting_product_name"
        return {
            "response": (
                f"I don't see \"{product_name}\" in our catalog — could you check the spelling? "
                "You can also describe the item and I'll do my best."
            ),
            "show_ticket_form": False,
            "escalated": False,
            "suggest_ticket": False,
            "prefilled_order_id": "",
        }

    # Found — reset failure counter
    session["stock_lookup_attempts"] = 0
    session.pop("pending_context", None)

    sizes, colors, stock_status, quantity, restock_date = row
    level = _stock_level_text(quantity)

    color_part = f" in {colors}" if colors and colors.lower() not in product_name.lower() else ""
    sizes_part = f" Available sizes: {sizes}." if sizes else ""

    if stock_status == "in_stock":
        response = f"The {product_name.title()}{color_part} is in stock — {level}.{sizes_part}"
    else:
        restock_part = f" We're expecting a restock on {restock_date}." if restock_date else " No restock date yet."
        response = (
            f"The {product_name.title()}{color_part} is currently out of stock.{restock_part}"
            f" Sizes normally available: {sizes}."
        )

    return {
        "response": response,
        "show_ticket_form": False,
        "escalated": False,
        "suggest_ticket": False,
        "prefilled_order_id": "",
    }


# ---------------------------------------------------------------------------
# MAIN ROUTER
# ---------------------------------------------------------------------------

def get_response(user_text: str, session: dict) -> dict:
    """
    Main entry point. Routes the user message to the appropriate handler.

    Parameters:
        user_text: Raw user input string.
        session:   Mutable dict representing the current user session state.
                   Keys used:
                     pending_context        — what the bot last asked for
                     order_lookup_attempts  — consecutive failed order lookups
                     stock_lookup_attempts  — consecutive failed stock lookups
                     escalated              — True once the session is escalated

    Returns:
        dict with keys:
          response          str
          show_ticket_form  bool
          suggest_ticket    bool
          escalated         bool
          prefilled_order_id str
    """
    try:
        if not user_text or not str(user_text).strip():
            return {
                "response": "I'm here to help! Ask me about an order status or whether an item is in stock.",
                "show_ticket_form": False,
                "escalated": session.get("escalated", False),
                "suggest_ticket": False,
                "prefilled_order_id": "",
            }

        intent = detect_intent(user_text)

        # ----- Immediate escalation -----
        if intent == "escalate_immediate":
            if session.get("escalated"):
                # Already connected — remind them the form is waiting
                return {
                    "response": "You're already connected — please fill in the form below so the agent has your details.",
                    "show_ticket_form": True,
                    "escalated": True,
                    "suggest_ticket": False,
                    "prefilled_order_id": "",
                }
            result = _handle_escalation("general")
            session["escalated"] = True
            return result

        # ----- Resolve pending context first -----
        pending = session.get("pending_context")

        if pending == "awaiting_order_number" or pending == "awaiting_order_number_retry":
            # User's reply should contain an order number
            order_id = _extract_order_id(user_text)
            session.pop("pending_context", None)
            return _handle_order_status(order_id, session)

        if pending == "awaiting_product_name":
            # Narrow search to stored candidates first (if any), else full catalog
            candidates = session.get("product_candidates", [])
            search_pool = candidates if candidates else _all_product_names()
            product, new_candidates = _extract_product_name(user_text, search_pool)
            session.pop("pending_context", None)

            if product:
                session.pop("product_candidates", None)
            elif new_candidates:
                session["product_candidates"] = new_candidates
            else:
                # Still nothing — count as a failed attempt
                session["stock_lookup_attempts"] = session.get("stock_lookup_attempts", 0) + 1
                session.pop("product_candidates", None)
            return _handle_stock(product, session)

        # ----- Route by fresh intent -----
        if intent == "order_status":
            order_id = _extract_order_id(user_text)
            return _handle_order_status(order_id, session)

        if intent == "stock":
            known = _all_product_names()
            product, candidates = _extract_product_name(user_text, known)
            if candidates:
                session["product_candidates"] = candidates
            else:
                session.pop("product_candidates", None)

            # If the user named something specific but it matches nothing in the
            # catalog at all (zero partial matches), redirect rather than loop.
            if product is None and not candidates:
                t = str(user_text).lower()
                leftover = set(re.sub(r'[^\w\s]', '', t).split()) - _STOP_WORDS
                if leftover:  # user named something specific, not a generic query
                    return {
                        "response": "I don't think we carry that — I can help with order tracking and stock checks for the items we sell, or connect you with a support agent.",
                        "show_ticket_form": False,
                        "escalated": False,
                        "suggest_ticket": False,
                        "prefilled_order_id": "",
                    }

            return _handle_stock(product, session)

        # ----- Unknown intent — last-chance product match -----
        # e.g. "are boots in" has no stock keyword but "boots" → "red boots"
        known = _all_product_names()
        product, candidates = _extract_product_name(user_text, known)
        if product or candidates:
            if candidates:
                session["product_candidates"] = candidates
            else:
                session.pop("product_candidates", None)
            return _handle_stock(product, session)

        if session.get("escalated"):
            # Already connected — scroll back to the waiting form
            return {
                "response": "I can help with order tracking and stock checks in the meantime — or fill in the form below and the agent will follow up.",
                "show_ticket_form": True,
                "escalated": True,
                "suggest_ticket": False,
                "prefilled_order_id": "",
            }
        result = _handle_escalation("out_of_scope")
        session["escalated"] = True
        return result

    except Exception:
        return {
            "response": "Something went wrong on my end — please try again in a moment.",
            "show_ticket_form": False,
            "escalated": False,
            "suggest_ticket": False,
            "prefilled_order_id": "",
        }


# ---------------------------------------------------------------------------
# BUILT-IN TEST SUITE
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 60)
    print("NORTHSTAR ASSISTANT — CHATBOT TEST SUITE")
    print("=" * 60)

    def run(text, session=None):
        s = session if session is not None else {}
        return get_response(text, s), s

    cases = [
        # (description, user_text, pre_session, validator_fn)
        (
            "Order found — shipped",
            "Where is my order 1001?",
            {},
            lambda r, s: "on its way" in r["response"].lower(),
        ),
        (
            "Order found — delayed",
            "Track order 1004",
            {},
            lambda r, s: "delay" in r["response"].lower(),
        ),
        (
            "Order found — delivery exception",
            "What's happening with order 1005?",
            {},
            lambda r, s: "exception" in r["response"].lower(),
        ),
        (
            "Order found — delivered",
            "Status of order 1003",
            {},
            lambda r, s: "delivered" in r["response"].lower(),
        ),
        (
            "Order not found (1st attempt)",
            "Order 9999 status",
            {},
            lambda r, s: "couldn't find" in r["response"].lower() and s.get("order_lookup_attempts") == 1,
        ),
        (
            "Order not found (2nd attempt → auto-escalate)",
            "Order 9999",
            {"order_lookup_attempts": 2},
            lambda r, s: r["show_ticket_form"] is True,
        ),
        (
            "No order ID given → ask for it",
            "Where is my order?",
            {},
            lambda r, s: s.get("pending_context") == "awaiting_order_number",
        ),
        (
            "Pending order number → user provides it",
            "It's 1002",
            {"pending_context": "awaiting_order_number"},
            lambda r, s: "red boots" in r["response"].lower(),
        ),
        (
            "Stock check — in stock, plenty",
            "Is the blue sneakers in stock?",
            {},
            lambda r, s: "in stock" in r["response"].lower() and "plenty" in r["response"].lower(),
        ),
        (
            "Stock check — low stock",
            "Do you have green jacket available?",
            {},
            lambda r, s: "only 2 left" in r["response"].lower(),
        ),
        (
            "Stock check — out of stock",
            "Do you have red boots?",
            {},
            lambda r, s: "out of stock" in r["response"].lower(),
        ),
        (
            # Specific item not in catalog → redirect, not clarifying question
            "Stock check — unknown product (specific item not in catalog)",
            "Is the purple hat available?",
            {},
            lambda r, s: "don't think we carry" in r["response"].lower(),
        ),
        (
            # Generic stock query (no product named) → clarifying question
            "Stock check — generic query → clarifying question",
            "What do you have in stock?",
            {},
            lambda r, s: s.get("pending_context") == "awaiting_product_name",
        ),
        (
            # In pending context, still no match → count as failure, escalate after 3
            "Stock check — repeated no-match in pending context → escalate",
            "The purple hat",
            {"stock_lookup_attempts": 2, "pending_context": "awaiting_product_name"},
            lambda r, s: r["show_ticket_form"] is True,
        ),
        (
            "Refund mention → immediate escalation",
            "I need a refund",
            {},
            lambda r, s: r["show_ticket_form"] is True,
        ),
        (
            "Frustration keyword → immediate escalation",
            "This is absolutely ridiculous",
            {},
            lambda r, s: r["show_ticket_form"] is True,
        ),
        (
            "Human request → immediate escalation",
            "I need to talk to a real person",
            {},
            lambda r, s: r["show_ticket_form"] is True,
        ),
        (
            "Out-of-scope query → escalation",
            "What time do you open?",
            {},
            lambda r, s: r["show_ticket_form"] is True,
        ),
        (
            "Already escalated → remind of waiting form",
            "Can I still get help?",
            {"escalated": True},
            lambda r, s: r["escalated"] is True and r["show_ticket_form"] is True,
        ),
        (
            "Empty string",
            "",
            {},
            lambda r, s: "i'm here" in r["response"].lower(),
        ),
    ]

    passed = 0
    for desc, text, pre_session, validator in cases:
        result, sess = run(text, pre_session)
        ok = validator(result, sess)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"\n[{status}] {desc}")
        if not ok:
            print(f"        Input:    {repr(text)}")
            print(f"        Response: {repr(result['response'])}")
            print(f"        Session:  {sess}")

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{len(cases)} passed")
    print("=" * 60)
