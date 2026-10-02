"""The payment and booking checkpoint for the browser.

The classifier tests are pure. The browser tests use the real sandbox browser (Docker, the sandbox image)
on real public demo pages: W3Schools' demo checkout form (card fields, a cart and a total) and httpbin's
demo order form. They go only as far as the confirmation step and then deny it: nothing is ever bought,
booked or submitted, and no card details are typed.
"""

from __future__ import annotations

import pytest

from jig.config import load_config
from jig.constants import EventType
from jig.policy.core import CORE_RULES, checkpoint_finding
from jig.runtime import Jig
from jig.tools.checkout import amounts, classify

from .sandbox_helpers import gated_call

DEMO_CHECKOUT = "https://www.w3schools.com/howto/tryhow_css_checkout_form.htm"
DEMO_ORDER = "https://httpbin.org/forms/post"


# The classifier -------------------------------------------------------------------------------------------
def _info(**over):
    base = {"url": "https://shop.example/basket", "host": "shop.example", "title": "Your basket", "site_name": "",
            "button": "Continue", "form_action": "", "fields": [], "frames": [], "headings": [], "text": ""}
    return {**base, **over}


def test_card_fields_make_it_a_payment_with_merchant_items_and_total():
    out = classify(_info(button="Continue to checkout", site_name="Demo Shop",
                         fields=[{"name": "cardnumber", "payment": True}, {"name": "email", "payment": False}],
                         text="Cart 4\nProduct 1 £15\nProduct 2 £5.50\nDelivery £3\nTotal £23.50"))
    assert out["checkout"] == "payment" and out["merchant"] == "Demo Shop"
    assert out["amount"] == "£23.50" and out["items"] == ["Product 1 £15", "Product 2 £5.50"]
    assert out["payment_fields"] == 1 and out["avatar_variant"] == "shopping"


def test_payment_provider_frames_are_recognised():
    out = classify(_info(button="Next", frames=["js.stripe.com", "fonts.example"]))
    assert out["checkout"] == "payment" and out["payment_providers"] == ["stripe.com"]


def test_a_booking_is_called_a_booking():
    out = classify(_info(url="https://restaurant.example/reserve", button="Confirm booking",
                         text="Table for 2\nSaturday 19:30\nDeposit £10"))
    assert out["checkout"] == "booking" and out["amount"] is None and out["items"] == ["Deposit £10"]


@pytest.mark.parametrize("info", [
    _info(url="https://search.example/", title="Search", button="Search"),
    _info(url="https://news.example/", title="Newsletter", button="Sign up"),
])
def test_ordinary_forms_are_not_checkouts(info):
    assert classify(info)["checkout"] is None


def test_amounts_find_the_total_and_skip_fees():
    total, items = amounts("Widget $9.99\nSubtotal $9.99\nVAT $2.00\nOrder total: $11.99\nThanks")
    assert total == "$11.99" and items == ["Widget $9.99"]


def test_the_checkpoint_is_a_core_rule_that_asks():
    rule = next(r for r in CORE_RULES if r.id == "payment-checkpoint")
    assert rule.decision.value == "ask"
    finding = checkpoint_finding({"checkout": "payment", "merchant": "Demo Shop", "amount": "£30"})
    assert finding.decision.value == "ask" and "Demo Shop" in finding.reason and "£30" in finding.reason
    assert checkpoint_finding({"checkout": None}) is None and checkpoint_finding(None) is None


# The real sandbox browser on real demo pages ----------------------------------------------------------------
@pytest.fixture
async def bjig(tmp_path, capabilities):
    config = load_config(data_dir=tmp_path / "data", sandbox_dir=tmp_path / "sandbox", sandbox_backend="container")
    runtime = Jig(config)
    await runtime.start(run_scheduler=False, check_capabilities=False)
    try:
        yield runtime
    finally:
        await runtime.stop()


@pytest.mark.network
async def test_jig_never_types_card_details(bjig):
    intent = "Fill in the W3Schools demo checkout form."
    """Through the gate the Sentinel may refuse first; the browser's own guard refuses regardless, so it is
    also called directly here, as the last line of defence."""
    from jig.errors import ToolError

    assert (await gated_call(bjig, "browser_open", {"url": DEMO_CHECKOUT}, intent=intent))[0].ok
    session = await bjig.container.browser()
    for selector in ("#ccnum", "#cvv", "#expmonth", "#cname"):
        refused, _ = await gated_call(bjig, "browser_fill", {"selector": selector, "value": "Jig"}, intent=intent)
        assert not refused.ok and refused.error_type in ("ToolError", "PolicyBlocked"), (selector, refused.error)
        for command, extra in (("fill", {"value": "4111111111111111"}), ("type", {"text": "123"})):
            with pytest.raises(ToolError, match="payment field"):
                await session.call(command, selector=selector, **extra)
    with pytest.raises(ToolError, match="payment field"):
        await session.call("login", username_selector="#fname", username="x", password_selector="#cvv",
                           password="y", submit_selector="input[type=submit]")
    info = await session.call("inspect_submit", selector="form")
    payment = {f["name"] for f in info["fields"] if f["payment"]}
    assert payment >= {"cardname", "cardnumber", "expmonth", "expyear", "cvv"}
    # The page also labels its name field "Accepted Cards" (two labels point at it), so the guard refuses
    # that one too: when in doubt, it refuses.
    assert {"email", "city", "zip"}.isdisjoint(payment)
    filled = await session.call("fill", selector="#email", value="jig-test@example.com")
    assert filled["url"] == DEMO_CHECKOUT


@pytest.mark.network
async def test_a_demo_checkout_shows_merchant_items_and_amount_then_is_denied(bjig):
    intent = "Go through the W3Schools demo checkout as far as the confirmation step."
    assert (await gated_call(bjig, "browser_open", {"url": DEMO_CHECKOUT}, intent=intent))[0].ok
    bjig.rules.create(tool="browser_*", decision="allow", note="test: try to make checkouts automatic")
    seen: list[dict] = []
    bjig.bus.add_listener(lambda e: seen.append(e.data) if e.type in (EventType.TOOL_CHECKOUT,
                                                                       EventType.AVATAR_STATE) else None)
    asked: list[dict] = []

    def deny(approval: dict) -> bool:
        asked.append(approval)
        return False

    outcome, _ = await gated_call(bjig, "browser_submit", {"selector": "form"}, intent=intent, on_approval=deny)
    assert outcome.error_type in ("ApprovalDenied", "PolicyBlocked"), outcome.error
    resolved = outcome.policy["resolved"]
    assert resolved["checkout"] == "payment" and resolved["merchant"] == "w3schools.com"
    assert resolved["amount"] == "$30" and "Product 1 $15" in resolved["items"]
    assert resolved["payment_fields"] >= 4
    assert any(f["rule"] == "payment-checkpoint" for f in outcome.policy["core"])
    if outcome.error_type == "ApprovalDenied":
        assert any(r["rule"] == "payment-checkpoint" for r in asked[0]["reasons"])
        assert asked[0]["resolved"]["amount"] == "$30"
    assert any(d.get("variant") == "shopping" for d in seen), "the avatar shows its shopping state"
    page = await (await bjig.container.browser()).call("read", format="text", max_chars=100)
    assert page["url"] == DEMO_CHECKOUT, "nothing was submitted"


@pytest.mark.network
async def test_clicking_a_checkout_button_needs_a_human_even_with_an_allow_rule(bjig):
    intent = "Look at the W3Schools demo checkout."
    assert (await gated_call(bjig, "browser_open", {"url": DEMO_CHECKOUT}, intent=intent))[0].ok
    bjig.rules.create(tool="browser_click", decision="allow", note="test")
    asked: list[dict] = []
    outcome, _ = await gated_call(bjig, "browser_click", {"selector": "input[type=submit]"}, intent=intent,
                                  on_approval=lambda a: asked.append(a) or False)
    assert outcome.error_type in ("ApprovalDenied", "PolicyBlocked"), outcome.error
    if outcome.error_type == "ApprovalDenied":
        assert any(r["rule"] == "payment-checkpoint" for r in asked[0]["reasons"])
    assert outcome.policy["resolved"]["checkout"] == "payment"


@pytest.mark.network
async def test_an_order_form_without_prices_is_still_a_checkpoint(bjig):
    intent = "Look at the httpbin demo pizza order form."
    assert (await gated_call(bjig, "browser_open", {"url": DEMO_ORDER}, intent=intent))[0].ok
    outcome, _ = await gated_call(bjig, "browser_submit", {"selector": "form"}, intent=intent,
                                  on_approval=lambda a: False)
    assert outcome.error_type in ("ApprovalDenied", "PolicyBlocked")
    resolved = outcome.policy["resolved"]
    assert resolved["checkout"] == "payment" and resolved["button"] == "Submit order"
    assert resolved["site"] == "httpbin.org"
