# Integrations — built now, switched on with a key later

Every integration below is written and tested. None of them needs an account to exist in the
codebase; each one turns on when its keys are set, and says so on the health check when they aren't.

## Switching one on

Set the variables, restart, done. No code changes.

| Integration | Variables | Where the value comes from | Time |
|---|---|---|---|
| **Web chat** | none | always on | — |
| **Shopify** | `SHOPIFY_TOKEN`, `SHOPIFY_STORE` | Client's admin → Settings → Apps → Develop apps → custom app with `read_orders`, `read_products`. No app review. | 2 min |
| **Availability (iCal)** | `ICAL_URLS` (comma-separated), `ICAL_LISTING` | Airbnb: Listing → Availability → Sync calendars → Export. Booking.com and Vrbo have the same. | 2 min |
| **WhatsApp** | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` | Meta app → WhatsApp product. Test number works with no business verification. | 20 min |
| **Model** | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Ollama locally (no key), or Groq / Google AI Studio free tier | 5 min |

`CONNECTORS=shopify,ical_availability` chooses which connectors this deployment enables.

## How they behave before they're configured

- The connector reports `not_configured` with the variable to set — it never half-works.
- Its tools are not offered to the model, so the agent can't call something that isn't there.
- The bot falls back to the built-in demo data (orders) or to documents (availability).

## How they behave when something breaks

Timeout, bad token, provider outage → the tool returns "couldn't check", the agent tells the
customer it can't verify right now, and hands off to a human. It never invents a status, a price
or an availability answer. Health shows `degraded` with the reason.

## WhatsApp specifics

- The webhook URL is `https://<your-host>/channels/whatsapp`; `WHATSAPP_VERIFY_TOKEN` is a string
  you invent and paste into Meta's setup form.
- Every inbound request must carry a valid `X-Hub-Signature-256`, or it's rejected with 401.
- Meta retries; repeated message ids are ignored, so a customer never gets the same answer twice.
- Meta's 24-hour rule: outside that window only pre-approved templates may be sent. We don't send
  templates, so a late reply waits for a human instead of failing loudly.

## Adding another connector

Implement `tools()` and `health()`, register it, write its tests against a mock transport. Nothing
in `app/agent/core.py` changes. Shopify and iCal are the two worked examples.
