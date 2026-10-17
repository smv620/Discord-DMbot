# Stripe integration: what is set, what is left

The owner's single list for Stripe (#1446). DMbot sends people to Stripe's hosted Checkout page
(they leave our site, pay, and come back); Stripe tells us it worked through a webhook. We hold
no card details and the site loads no Stripe.js.

## Values to replace

Nothing in the code is a placeholder. The values that differ per environment are settings, set
with `scripts/set-key` or `scripts/update-env` on the server (never committed; see
`.env.example`):

| Setting | What it is |
|---|---|
| `STRIPE_SECRET_KEY` | The secret key: `sk_test_...` while testing, `sk_live_...` for real payments. The key itself says which mode DMbot is in. |
| `PAYMENT_WEBHOOK_SECRET` | The signing secret of the webhook endpoint (`whsec_...`, 16 characters or more). |
| `STRIPE_PRICE_TABLE`, `STRIPE_PRICE_TWO_TABLES`, `STRIPE_PRICE_GUILD` | The `price_...` id of each plan, from the Stripe Dashboard. |
| `STRIPE_PRICE_EXTRA_HOURS` | The +10 hours price. |
| `STRIPE_PRICE_RECAP_AUDIO` | The narrated-recaps add-on. Leave empty until the owner makes it. |
| `STRIPE_MANAGED_PAYMENTS` | `1` to ask Stripe to be the seller. Leave `0` until the sandbox run (#1096) has checked it. |

Names have no `VITE_` prefix: the site (`web/`) never talks to Stripe, only the API in
`core/src/dmbot/web/` does.

## Configured parameters

Every Checkout Session DMbot creates carries the parameters from the owner's Checkout Studio,
in [`CHECKOUT_STUDIO` in `core/src/dmbot/web/stripe.py`](core/src/dmbot/web/stripe.py). They
are sent on all three calls: a plan (`mode=subscription`), the +10 extra hours
(`mode=payment`) and the recap audio add-on (`mode=subscription`).

| Parameter | Value |
|---|---|
| `ui_mode` | `hosted_page` |
| `billing_address_collection` | `auto` |
| `phone_number_collection[enabled]` | `false` |
| `automatic_tax[enabled]` | `true` |
| `allow_promotion_codes` | `true` |
| `submit_type` | `auto` |
| `integration_identifier` | `hosted_web_0001` |
| `origin_context` | `web` |
| `payment_method_collection` | `always`, on the two subscriptions only (never on the one-time payment) |

Kept as they were (real values, not part of Checkout Studio): `mode`, `success_url`,
`cancel_url` and `line_items`. Also kept, because DMbot's own rules depend on them:

- `metadata[user_id]` (and `metadata[item]`), `subscription_data[metadata][...]`: how the
  webhook knows whose payment it is, and tells extra hours and the add-on from a plan.
- `customer` or `customer_email`: ties the payment to the person (the email only ever goes in
  the request body, never in the address).
- `expires_at`: with the idempotency key, so a double click makes one checkout, not two.
- `managed_payments[enabled]`: only while `STRIPE_MANAGED_PAYMENTS=1`.
- `customer_update[address]=auto`: added only when there is a `customer`. Automatic tax needs
  the customer's address, and Stripe refuses it unless Checkout may save the address to the
  customer.

### What was checked against Stripe's reference

(https://docs.stripe.com/api/checkout/sessions/create, read 2026-10-16.)

1. **`ui_mode`**: the API version DMbot sends is pinned in the code (`API_VERSION`, now
   `2026-09-30.endive`), and on it the values are `hosted_page`, `embedded_page`, `form` and
   `elements`; `hosted_page` is the default. Older API versions call it `hosted`. If
   `API_VERSION` is ever moved back to an older one, change `ui_mode` with it. (Moved to endive on
   2026-10-17, #1703: the stripe-python 16.0.0 changelog lists no change to `ui_mode`; the sandbox run
   (#1096) confirms it.)
2. **`integration_identifier`** (a string) and **`origin_context`** (an enum, `web` among its
   values) are both listed as Checkout Session create parameters on this version, so they are sent.
3. **`automatic_tax[enabled]=true`** needs **Stripe Tax turned on in the Dashboard** (Settings,
   Tax) with your tax registrations and origin address set, or Stripe answers with an error
   and nobody can buy. **Owner: do this before the sandbox run.** For a customer that already
   exists, `customer_update[address]=auto` is sent (above).

## How the flow works

1. The person presses a button on My Account or the Prices page. The site calls the API
   (`POST /billing/checkout`, `/billing/extra-hours` or `/billing/recap-audio`).
2. The API creates a Checkout Session (`core/src/dmbot/web/stripe.py`) and answers with its
   address, `https://checkout.stripe.com/...`. The browser goes there.
3. The person pays on Stripe's page and is sent back to the site.
4. Stripe sends a signed event to the webhook (`POST /webhooks/stripe` in
   `core/src/dmbot/web/app.py`). The signature is checked, and only then is the plan started,
   changed or ended. A plan starts when the first invoice is **paid**, not when the page is
   opened.

## Test cards and test mode

- Use a `sk_test_` key and Stripe's test mode. The key decides the mode; an event from the
  other mode is ignored.
- Cards: `4242 4242 4242 4242` pays; `4000 0000 0000 0341` is accepted at checkout and fails
  on the next charge; `4000 0000 0000 9995` is declined for lack of funds. Any future expiry,
  any CVC and postcode. Full list: https://docs.stripe.com/testing
- Forward webhooks to a test server with the Stripe CLI (`stripe listen`) or add a test-mode
  endpoint in the Dashboard (Developers, Webhooks) on the **same API version** as
  `API_VERSION`.

## Next steps

- [ ] Turn on Stripe Tax in the Dashboard (see above).
- [ ] The sandbox run: #1096.
- [ ] Set the `STRIPE_PRICE_*` ids for the environment you are testing in.
- [ ] In that run, try each purchase with `STRIPE_MANAGED_PAYMENTS=1` as well as `0`: we could not check from the reference whether Managed Payments accepts every parameter above (promotion codes, phone collection, automatic tax), and a refusal would show only when it is on.
- [ ] Decide on `STRIPE_MANAGED_PAYMENTS` after that run.

## Resources

- Stripe support: https://support.stripe.com
- Stripe's MCP server, for asking about your account from an assistant: https://docs.stripe.com/mcp
