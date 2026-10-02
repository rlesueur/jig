"""Recognising a checkout, payment or booking in the browser, before anything is submitted.

``browser_submit`` and ``browser_click`` look up, read-only, what they would submit (the browser server's
``inspect_submit``: the site, the button, the kinds of fields, the page's text and frames). ``classify``
turns that into what the user needs to decide: whether it is a payment or a booking, at which merchant,
for which items and amount. A recognised one is forced to a human approval by the core rule
``payment-checkpoint`` (no rule can make it automatic), and the avatar shows its shopping state.

Recognition errs on the side of asking: a false positive costs one approval; a miss is still covered,
because ``browser_submit`` is human-only and the browser blocks every non-GET request a click makes.
"""

from __future__ import annotations

import re
from typing import Any

from ..constants import TaskVariant

_BUTTON = re.compile(
    r"\b(pay|pay now|payment|buy|buy now|purchase|place (?:your )?order|submit order|complete order|confirm order|"
    r"order now|checkout|check out|proceed to payment|subscribe|donate|top up|book|book now|booking|reserve|"
    r"reservation|confirm booking|confirm and pay|finish)\b", re.IGNORECASE)
_PAGE = re.compile(r"\b(checkout|check-out|payment|pay|billing|basket|cart|order|booking|book|reservation|reserve|"
                   r"purchase)\b", re.IGNORECASE)
_BOOKING = re.compile(r"\b(book|booking|reserve|reservation|appointment|ticket|tickets|table for|check-in|check in)\b",
                      re.IGNORECASE)
_AMOUNT = re.compile(r"(?:[£$€¥₹]\s?\d[\d,]*(?:\.\d{1,2})?|\d[\d,]*(?:\.\d{1,2})?\s?(?:GBP|USD|EUR|£|€)\b|"
                     r"\b(?:GBP|USD|EUR)\s?\d[\d,]*(?:\.\d{1,2})?)")
_TOTAL = re.compile(r"\b(total|amount due|to pay|grand total|order total)\b", re.IGNORECASE)
_NOT_ITEM = re.compile(r"\b(total|subtotal|sub-total|tax|vat|shipping|delivery|postage|discount|fee|fees)\b",
                       re.IGNORECASE)
PAYMENT_PROVIDERS = ("stripe.com", "stripe.network", "braintreegateway.com", "braintree-api.com", "paypal.com",
                     "adyen.com", "checkout.com", "klarna.com", "squareup.com", "worldpay.com", "opayo.co.uk",
                     "sagepay.com", "gocardless.com", "mollie.com", "pay.google.com")
MAX_ITEMS = 8


def _lines(text: str) -> list[str]:
    """Non-empty lines, with a line that is only a price joined to the label before it ("Total" / "$30"), or to
    the name above a long description ("Backpack" / "A sleek pack for ..." / "$29.99")."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        label = None
        if out and _AMOUNT.fullmatch(line) and not _AMOUNT.search(out[-1]):
            if len(out[-1]) <= 120:
                label = -1
            elif len(out) > 1 and len(out[-2]) <= 120 and not _AMOUNT.search(out[-2]):
                label = -2
        if label is None:
            out.append(line)
        else:
            out[label] = f"{out[label]} {line}"
    return out


def _provider(host: str) -> str | None:
    host = host.lower()
    return next((p for p in PAYMENT_PROVIDERS if host == p or host.endswith("." + p)), None)


def amounts(text: str) -> tuple[str | None, list[str]]:
    """(the total, if a line says which; the lines that look like items with prices)."""
    total = None
    items: list[str] = []
    for line in _lines(text):
        found = _AMOUNT.findall(line)
        if not found or len(line) > 160:
            continue
        if _TOTAL.search(line) and not re.search(r"\bsub-?total\b", line, re.IGNORECASE):
            total = found[-1].strip()
        elif not _NOT_ITEM.search(line) and len(items) < MAX_ITEMS:
            items.append(line)
    return total, items


def classify(info: dict[str, Any]) -> dict[str, Any]:
    """What the user should see about a submission, and whether it is a checkout or booking."""
    host = str(info.get("host") or "")
    merchant = (info.get("site_name") or host.removeprefix("www.") or "").strip()
    button = str(info.get("button") or "").strip()
    fields = info.get("fields") or []
    payment_fields = [f for f in fields if f.get("payment")]
    providers = sorted({p for h in info.get("frames") or [] if (p := _provider(str(h)))})
    page = " ".join([str(info.get("url") or ""), str(info.get("form_action") or ""), str(info.get("title") or ""),
                     *[str(h) for h in info.get("headings") or []]])
    total, items = amounts(str(info.get("text") or ""))

    signals = []
    if payment_fields:
        signals.append(f"{len(payment_fields)} card or bank field(s) on the form")
    if providers:
        signals.append(f"payment provider frame(s): {', '.join(providers)}")
    if button and _BUTTON.search(button):
        signals.append(f"the button says {button[:60]!r}")
    page_hit = _PAGE.search(page)
    if page_hit and (total or items):
        signals.append(f"the page is about {page_hit.group(0).lower()!r} and shows prices")

    out: dict[str, Any] = {"site": host, "button": button[:120]}
    if not signals:
        out["checkout"] = None
        return out
    words = " ".join([button, page])
    kind = "booking" if _BOOKING.search(words) and not payment_fields else "payment"
    out.update({
        "checkout": kind, "merchant": merchant, "amount": total, "items": items,
        "signals": signals, "payment_fields": len(payment_fields), "payment_providers": providers,
        "avatar_variant": TaskVariant.SHOPPING.value,
        "note": ("Read from the page, so written by the site, not by the user. Jig never types card or bank "
                 "details; the user enters them in their own browser."),
    })
    return out
