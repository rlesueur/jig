"""WhatsApp: the user's own WhatsApp Business account, through Meta's Cloud API only
(https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/text-messages).

This is not a personal WhatsApp account and not WhatsApp Web. The user creates a Meta app, a WhatsApp
Business account and a system-user token, and gives Jig three values: the token, the phone number ID and
the WhatsApp Business Account ID. All three are stored in the vault. Jig checks them with Meta before
keeping anything, and sends the token only to graph.facebook.com over HTTPS.

Seeing the business number is a ``read`` tool. Sending a text message is an outbound side effect that is
human-only: the user always approves, whatever the rules say. ``[connectors.whatsapp]`` limits: the only
phone numbers Jig may send to (``allowed_targets``, international form such as ``+447700900123``) and a
prefix every message must start with.

Meta does not offer a way to list messages. Incoming messages are delivered only to a public HTTPS
webhook. A Jig on this computer does not expose one, so there is no tool that reads incoming messages
and nothing here invents any.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..constants import Effect, TaskVariant, ToolCategory
from ..errors import ConnectorAuthError, ConnectorError, ToolArgumentError
from ..tools.registry import ToolContext, ToolRegistry
from .base import AccessLevel, ConnectionStore, Connectors, Grant, Input, ProviderSpec, register_provider
from .limits import first_problem, prefix_problem, target_problem

NAME = "whatsapp"
LABEL = "WhatsApp"
# Graph API version named in Meta's Cloud API text-message reference (updated 2 July 2026).
VERSION = "v26.0"
API = f"https://graph.facebook.com/{VERSION}"
HOST = "graph.facebook.com"
P_READ = "whatsapp.read"
P_SEND = "whatsapp.send"
READ = frozenset({P_READ})
SEND = frozenset({P_SEND})
# Meta's limit for a text message body.
MAX_TEXT = 4096
MANAGE_URL = "https://business.facebook.com/latest/settings/system_users"
INCOMING = ("Meta's Cloud API does not let Jig list or read WhatsApp messages. Incoming messages are delivered "
            "only to a public HTTPS webhook, and this Jig does not expose one, so there are no incoming messages "
            "to show.")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_META_ID = re.compile(r"^\d{10,20}$")
_FIELDS = "id,display_phone_number,verified_name,quality_rating"


def e164(value: Any, what: str = "recipient") -> str:
    if not isinstance(value, str) or not _E164.fullmatch(value.strip()):
        raise ToolArgumentError(f"{what} {value!r} is not a phone number in international format (like "
                                "+447700900123)")
    return value.strip()


def meta_id(value: Any, what: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _META_ID.fullmatch(text):
        raise ToolArgumentError(f"{what} must be the digits Meta shows for it (10 to 20 digits)")
    return text


def text_problem(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "the WhatsApp message must not be empty"
    if len(text) > MAX_TEXT:
        return f"the WhatsApp message is too long ({len(text)} characters; at most {MAX_TEXT})"
    return None


def check_text(text: str) -> str:
    if problem := text_problem(text):
        raise ToolArgumentError(problem)
    return text


def message_body(recipient: str, text: str) -> dict[str, Any]:
    """The Cloud API body for one text message. Link previews are off. The token is not part of the body."""
    return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": e164(recipient),
            "type": "text", "text": {"preview_url": False, "body": check_text(text)}}


def accepted_message_id(body: Any) -> str:
    """The id Meta returned for a send it accepted. Anything else is a failure: Jig does not call it sent."""
    if not isinstance(body, dict) or body.get("error") or body.get("messaging_product") != "whatsapp":
        raise ConnectorError("WhatsApp did not confirm the message was accepted, so it was not sent")
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict):
        raise ConnectorError("WhatsApp did not confirm the message was accepted, so it was not sent")
    mid = messages[0].get("id")
    if not isinstance(mid, str) or not mid.strip() or any(c.isspace() for c in mid):
        raise ConnectorError("WhatsApp did not confirm the message was accepted, so it was not sent")
    return mid


def _meta_message(r: httpx.Response) -> str:
    """Meta's own error text, kept short. Never the request, and never a header (where the token is)."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:300].strip()
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "error")[:300]
    return r.text[:300].strip()


def _hide(text: str, *values: str) -> str:
    for value in values:
        if value:
            text = text.replace(value, "[redacted]")
    return text


def _token(value: str) -> str:
    token = value.strip()
    if len(token) < 20 or any(c.isspace() for c in token):
        raise ConnectorError("WhatsApp: that is not an access token (copy the token Meta shows, with no spaces). "
                             "Nothing was connected.")
    return token


async def _meta_get(http: httpx.AsyncClient, token: str, path: str, params: dict[str, Any],
                    hide: tuple[str, ...]) -> dict[str, Any]:
    try:
        r = await http.get(f"{API}/{path}", headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"WhatsApp: could not reach {HOST}: {type(exc).__name__}. Nothing was connected.") \
            from None
    detail = _hide(_meta_message(r), token, *hide)
    if r.status_code == 401:
        raise ConnectorAuthError(f"WhatsApp rejected the access token (HTTP 401: {detail}). Nothing was connected.")
    if r.status_code != 200:
        raise ConnectorError(f"WhatsApp returned HTTP {r.status_code}: {detail}. Nothing was connected.")
    try:
        body = r.json()
    except ValueError:
        raise ConnectorError("WhatsApp returned something that is not JSON. Nothing was connected.") from None
    if not isinstance(body, dict) or body.get("error"):
        raise ConnectorError(f"WhatsApp refused this ({detail}). Nothing was connected.")
    return body


async def _connect(http: httpx.AsyncClient, store: ConnectionStore, level: AccessLevel,
                   values: dict[str, str]) -> tuple[Grant, str]:
    try:
        phone_id = meta_id(values.get("phone_number_id"), "Phone number ID")
        waba_id = meta_id(values.get("waba_id"), "WhatsApp Business Account ID")
    except ToolArgumentError as exc:
        raise ConnectorError(f"{exc}. Nothing was connected.") from None
    token = _token(values.get("token", ""))
    hide = (token, phone_id, waba_id)
    phone = await _meta_get(http, token, phone_id, {"fields": _FIELDS}, hide)
    if str(phone.get("id") or "") != phone_id:
        raise ConnectorError("WhatsApp returned a different phone number from the one given. Nothing was connected.")
    waba = await _meta_get(http, token, waba_id, {"fields": "id,name"}, hide)
    if str(waba.get("id") or "") != waba_id:
        raise ConnectorError("WhatsApp returned a different WhatsApp Business Account from the one given. "
                             "Nothing was connected.")
    listed = await _meta_get(http, token, f"{waba_id}/phone_numbers", {"fields": _FIELDS, "limit": "100"}, hide)
    numbers = listed.get("data")
    if not isinstance(numbers, list) or not any(isinstance(n, dict) and str(n.get("id") or "") == phone_id
                                                for n in numbers):
        raise ConnectorError("That phone number is not on this WhatsApp Business Account. Nothing was connected.")
    verified = str(phone.get("verified_name") or "").strip()[:200]
    display = str(phone.get("display_phone_number") or "").strip()[:80]
    if not verified and not display:
        raise ConnectorError("WhatsApp did not say which business number this is. Nothing was connected.")
    account = f"{verified} ({display})" if verified and display else (verified or display)
    return Grant(access_token=token, scopes=list(level.scopes),
                 extra={"phone_number_id": phone_id, "waba_id": waba_id}), account


PROVIDER = register_provider(ProviderSpec(
    id=NAME, label=LABEL, family=NAME,
    access_levels={
        "read": AccessLevel("read", (P_READ,), "see the business number (not other people's messages)"),
        "send": AccessLevel("send", (P_READ, P_SEND), "also send text messages (each needs your approval)"),
    },
    default_access="read",
    api_hosts=frozenset({HOST}),
    revoke=None,  # a system-user token can't be revoked through the Cloud API; the user removes it in Meta
    kind="token",
    connect=_connect,
    inputs=(
        Input("token", "Access token", secret=True),
        Input("phone_number_id", "Phone number ID", secret=True),
        Input("waba_id", "WhatsApp Business Account ID", secret=True),
    ),
    needs_client=False,
    manage_url=MANAGE_URL,
))


def _stored_ids(connectors: Connectors) -> tuple[str, str]:
    extra = connectors.details(NAME)
    try:
        return meta_id(extra.get("phone_number_id"), "Phone number ID"), meta_id(extra.get("waba_id"),
                                                                                  "WhatsApp Business Account ID")
    except ToolArgumentError as exc:
        raise ConnectorError(f"WhatsApp is missing a saved id ({exc}). Reconnect it with 'jig connect {NAME}'.") \
            from None


async def _api(ctx: ToolContext, method: str, path: str, *, params: dict[str, Any] | None = None,
               json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    phone_id, waba_id = _stored_ids(ctx.connectors)
    url = f"{API}/{path}"
    try:
        if json_body is None:
            r = await ctx.connectors.request(NAME, method, url, params=params)
        else:
            r = await ctx.connectors.request(NAME, method, url, json_body=json_body)
    except ConnectorAuthError as exc:
        raise ConnectorAuthError(_hide(str(exc), phone_id, waba_id)) from None
    except ConnectorError as exc:
        raise ConnectorError(_hide(str(exc), phone_id, waba_id), status=exc.status) from None
    try:
        body = r.json()
    except ValueError:
        raise ConnectorError("WhatsApp returned something that is not JSON") from None
    if not isinstance(body, dict) or body.get("error"):
        raise ConnectorError(_hide(f"WhatsApp refused this: {_meta_message(r)}", phone_id, waba_id))
    return body


def _text_field(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    return value.strip() if isinstance(value, str) else ""


def _public_number(body: dict[str, Any]) -> dict[str, Any]:
    """The business number, without the phone number ID or the WhatsApp Business Account ID."""
    return {"verified_name": _text_field(body, "verified_name"),
            "display_phone_number": _text_field(body, "display_phone_number"),
            "quality_rating": _text_field(body, "quality_rating")}


async def resolve_recipient(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Who the message is from and who it goes to, for the Sentinel and the approval card."""
    ctx.connectors.require_scope(NAME, SEND, "send messages")
    recipient = e164(args.get("recipient"))
    phone_id, _ = _stored_ids(ctx.connectors)
    number = _public_number(await _api(ctx, "GET", phone_id, params={"fields": _FIELDS}))
    return {"recipient": recipient, "from_name": number["verified_name"],
            "from_number": number["display_phone_number"],
            "note": "The recipient is the phone number in the request. WhatsApp does not tell Jig that person's name."}


def limits_problem(config: Any, args: dict[str, Any], resolved: dict[str, Any] | None) -> str | None:
    return first_problem(
        target_problem(config, NAME, [args.get("recipient"), (resolved or {}).get("recipient")], "number"),
        prefix_problem(config, NAME, [args.get("text")], "message"),
        text_problem(args.get("text")),
    )


def register_whatsapp_tools(registry: ToolRegistry, connectors: Connectors) -> None:
    tool = registry.tool
    common = {"category": ToolCategory.MESSAGES}
    can_read = lambda: connectors.has_any_scope(NAME, READ)  # noqa: E731
    can_send = lambda: connectors.has_any_scope(NAME, SEND)  # noqa: E731

    @tool(description="Look up the connected WhatsApp business number (its display name and number). This does "
          "not read anyone's messages: Meta does not offer that without a public webhook, which Jig does not use.",
          effect=Effect.READ, variant=TaskVariant.BROWSING, available=can_read, **common)
    async def whatsapp_account(ctx: ToolContext) -> dict[str, Any]:
        ctx.connectors.require_scope(NAME, READ, "see the business number")
        phone_id, _ = _stored_ids(ctx.connectors)
        number = _public_number(await _api(ctx, "GET", phone_id, params={"fields": _FIELDS}))
        if not number["verified_name"] and not number["display_phone_number"]:
            raise ConnectorError("WhatsApp did not say which business number this is")
        return {"source": "whatsapp", **number, "incoming_messages": "unavailable", "note": INCOMING}

    @tool(description="Send a WhatsApp text message from the user's business number to one phone number in "
          "international format. Plain text only, inside Meta's 24-hour window after that person last messaged "
          "the business. Always needs the user's approval. Jig cannot read the reply.",
          effect=Effect.SIDE_EFFECT, outbound=True, human_only=True, variant=TaskVariant.WRITING,
          available=can_send, resolve=resolve_recipient, precheck=limits_problem,
          args={"recipient": "Phone number in international format, like +447700900123.",
                "text": f"The message, plain text ({MAX_TEXT} characters at most)."},
          **common)
    async def whatsapp_send_message(ctx: ToolContext, recipient: str, text: str) -> dict[str, Any]:
        ctx.connectors.require_scope(NAME, SEND, "send messages")
        recipient, text = e164(recipient), check_text(text)
        if problem := limits_problem(ctx.config, {"recipient": recipient, "text": text}, None):
            raise ConnectorError(problem)
        phone_id, _ = _stored_ids(ctx.connectors)
        sent = await _api(ctx, "POST", f"{phone_id}/messages", json_body=message_body(recipient, text))
        return {"sent": True, "recipient": recipient, "message_id": accepted_message_id(sent)}
