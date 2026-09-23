# Spec: integrations (channels in, business systems out)

## Objective

Make every integration a day's work instead of a week's, and make a broken integration a
non-event: the bot says it can't check right now and hands off, never guesses.

Two separate things share this spec because they share the same machinery (credentials, retries,
health, failure behaviour):

- **Channels** — how a customer message arrives and how a reply goes back: web widget, WhatsApp, email.
- **Connectors** — where facts come from: Shopify, availability calendars, CRM, clinic calendars.

## Channels

Every channel collapses into one call:

```python
@dataclass(frozen=True)
class InboundMessage:
    conversation_id: str    # stable per customer per channel
    channel: Channel
    text: str
    customer_handle: str    # email, phone, or widget session
    provider_message_id: str  # for idempotency
```

A `ChannelAdapter` does three things: verify the request is really from the provider, turn it into an
`InboundMessage`, and send replies (it implements the existing `OutboundSender`).

Rules that apply to every channel, without exception:

- **Signature verification.** An unverified webhook is rejected with 401 before anything is read.
  Providers sign; we check. No exceptions for "just testing".
- **Idempotency.** Providers retry. A `provider_message_id` already seen is acknowledged and
  ignored — the bot must never answer the same message twice.
- **Fast acknowledgement.** WhatsApp and email providers time out in seconds and retry on delay.
  The webhook stores the message, returns 200, and answers on a background task.
- **No secrets in logs.** Message bodies are not logged at INFO; tokens never.

| Channel | Inbound | Outbound | Notes |
|---|---|---|---|
| Web widget | POST from the page | in the HTTP response | No provider, no signature; rate-limited per session |
| WhatsApp | Meta Cloud API webhook (HMAC-SHA256, `X-Hub-Signature-256`) | Graph API send | 24-hour window rule: outside it, only approved templates |
| Email | Provider inbound webhook | SMTP or provider API | Strip quoted history; thread by `Message-ID` |

## Connectors

```python
class Connector(Protocol):
    name: str
    def tools(self) -> list[ToolSchema]: ...          # what the agent may call
    def health(self) -> Health: ...                    # ok | degraded | broken + reason
```

| Connector | Gives the agent | Credentials | Why this approach |
|---|---|---|---|
| `shopify` | Order status, product price and stock | Per-store **custom app** token | No app review; the owner creates it in admin in two minutes |
| `ical_availability` | Which dates are free | One or more public iCal URLs | Airbnb, Booking.com and Vrbo all publish per-listing feeds — covers small properties with no PMS |
| `crm_webhook` | Pushes a qualified lead out | A webhook URL | Works with Zapier, Make, Sheets, any CRM |
| `google_calendar` | Free/busy for appointment requests | OAuth (read-only) | Never writes; an appointment is a request for a human |

Shared rules:

- **Read-only by default.** A connector that could change something in the client's system needs an
  explicit decision, per connector, recorded in an ADR. Today none of them write.
- **Fail safe.** Timeout, 5xx, bad credentials → the tool returns "couldn't check", the agent says so
  and escalates. It never invents a status.
- **Normalised results.** An order is an `OrderStatus` whether it came from Shopify or demo data,
  so agent code never learns a vendor's shape.
- **Cached briefly.** Product and availability lookups cache for 60 seconds; order status never caches.
- **Credentials are per client**, stored outside the repo, with an `expires_at` where the provider
  has one. `health()` surfaces an expiring token before a customer finds it.

## Acceptance criteria

- Every channel: a test proving a bad signature is rejected, and one proving a repeated
  `provider_message_id` produces exactly one reply
- Shopify: order lookup still enforces the email match; a wrong email leaks nothing (same property
  test as the demo data)
- Any connector down → the conversation ends in a handoff, never a guess (test per connector)
- `health()` for every configured connector shows on the admin console, with the reason when broken
- A new connector can be added without touching `app/agent/core.py` (demonstrated by the second one)

## Out of scope

Writing to client systems (creating orders, confirming bookings, issuing refunds). Marketplace apps
and OAuth flows for Shopify — per-store custom app tokens only. Direct PMS partnerships.
