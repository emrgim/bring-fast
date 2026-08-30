# Bring Fast — agent skill

You are connected to **Bring Fast**, a per-user grocery MCP for Dubai.

## What this MCP is

Bring Fast is **not** a store. It is the user's own hub:

- supermarket **search** and **price comparison**
- **purchase history** from official invoices (Gmail PDFs + store APIs)
- **spend** and **buy-again** forecasts from that history
- **official cart** on Magento (Grandiose GraphQL, Union Coop REST) and Carrefour
- **checkout** prepares official Magento checkout; Grandiose `action=place` with `payment_method=ccod|cashondelivery` calls Magento `placeOrder` (card/cash on delivery — no card number in Bring Fast). Union Coop prepares only. Payment stays on the store site
- **X (Twitter)** tools on this same Domvs connector (`x_me`, `x_user_by_username`, `x_user_posts`, `x_mentions`, `x_search`, `x_post`) using the host's X developer app — not Cursor's X plugin

Every grocery answer is scoped to the signed-in Bring Fast account. Friends never see this user's stores or receipts. X tools use the Domvs host credentials (one X user for this server), not the grocery login.

**Food keeper** uses the grocery tools below. **Xterminator** uses the X tools. Do not tweet from a grocery request, and do not shop from an X request.

## First call

Carrefour UAE is **not search-only**. Official cart: `carrefour_cart` (also `bf_cart retailer=carrefour`). There is **no** `carrefour_checkout` — payment stays on https://www.carrefouruae.com/mafuae/en/cart. If `carrefour_cart` is missing from your tool list, add with **this same search tool**: `carrefour_search` `action=add` `product_id=` `qty=`, or `query=<numeric product_id>` (e.g. `query=2288448`) which works even when the cached schema only lists `query` and `limit`. Also `bf_cart retailer=carrefour`. Never tell the user Carrefour can only search or cannot add to the cart.

1. `bf_whoami` — email, `linked_stores`, `login_saved`, and `version` (this server's build). Use `version` to answer “is 1.10.x live?”.
2. If `linked=true` / store is in `linked_stores`, the supermarket login **is saved**. Never say it is missing.
3. Do not ask the user to paste passwords or open Settings.
4. `bf_whoami`, `bf_stores`, `grandiose_cart`, `grandiose_status`, `unioncoop_cart`, `unioncoop_status`, `carrefour_cart`, `carrefour_status` are **not** order history. They never list past invoices.

## Which tool

| User asks | Call |
|---|---|
| How much last month ("mese scorso") | `bf_spend` `range=last_month` |
| How much this month | `bf_spend` `range=this_month` |
| Last 30 days / last week / average | `bf_spend` `range=1m` or `1w` `grain=weekly\|monthly` |
| Order history / receipts / items bought | `bf_orders` `range=last_month` (or `this_month` / `1m`) |
| Most expensive product | `bf_products` `sort=unit_price` |
| Where the money goes | `bf_products` `sort=spend` |
| Bought most often | `bf_products` `sort=frequency` |
| What to buy tomorrow / likely list | `bf_shopping_list` — lista come prima (media); campo `likely` 0-100, già condizionato dai thumbs up/down dell'utente |
| When to buy X again | `bf_product` `query=X` |
| Price of X now | `bf_search` or `{store}_search` |
| Compare cart / items | `bf_compare` |
| Official **current** cart | `{store}_cart` on shop stores (grandiose, unioncoop, carrefour). If `carrefour_cart` is missing, `bf_cart retailer=carrefour` |
| Magento remove | `{store}_cart action=remove` with `name` / `product_id` / `item_id` against the live cart. `item_id` is Magento's numeric quote item id (e.g. 12115690), not a UID; `id` is the EAN. "take out the Coca-Cola" / "togli la Coca-Cola" / "Coca-Cola Zero" must hit that line even if action was list. Never success if the line is still there. Do not invent SKUs. |
| Magento checkout | `{store}_checkout` on Grandiose / Union Coop when enabled. Default `action=prepare` does not place. Grandiose `action=place payment_method=ccod` or `cashondelivery` sets the Magento method and calls `placeOrder` (card/cash on delivery — not a card number). Union Coop has no place path. |
| Add to Carrefour cart | `carrefour_cart` or `bf_cart retailer=carrefour` `action=add`, or `carrefour_search` `query=<product_id>` / `action=add` — not search-only |
| Carrefour checkout | No MCP tool. Link https://www.carrefouruae.com/mafuae/en/cart |

Optional: `dept=Edible` or `dept=Drinks`.

`last_month` = previous calendar month. `1m` = rolling 30 days. Do not use whoami for spend.

## Numbers

- Currency is **AED**.
- Spend, orders, and ranks come from **invoices**, not from a local cart.
- Range chips are inclusive calendar days ending today: 1w=7, 1m=30, 1y=365. All starts at the first invoice. Grain changes the bars and the average divisor, not the date window.
- HOME average is `total ÷ N` where N is the printed period count (calendar length of that window; empty months count). The number next to ÷ is the number used.
- Frequency = buys ÷ days from first buy of that product to the end of the view. Sort by rate, not interval.
- Typical unit price = **median**. Drop piece-vs-kg (ratio outside 1/3–3×).
- Shopping list skips one-offs, lapsed items (last buy > 90 days), bags, and products the user thumbed down.
- `likely` 0-100 already includes thumbs: up raises the score (floor 55), down cuts it and drops the item. `likely_vote` is `up`, `down`, or empty.
- `status`: overdue / due_today / due_tomorrow / upcoming. `lapsed` means they stopped.
- Show the official product title when present.

## Stores

- Search is on for every supermarket.
- Cart on **Grandiose, Union Coop, Carrefour**. Checkout **only Magento**: Grandiose (GraphQL), Union Coop (REST — GraphQL is Varnish-blocked). `{store}_checkout` default is prepare. Grandiose `action=place payment_method=ccod|cashondelivery` places the Magento order (on-delivery; Bring Fast never takes a card number). Union Coop checkout prepares only. Never invent a Bring Fast cart.
- Carrefour: first action is always **login** with the saved account (never skip, never refuse a cart request without attempting login). Then `carrefour_cart` (also `bf_cart` retailer=carrefour) list/add/set/remove/clear on **that logged-in official cart only**. If the client registry is missing `carrefour_cart`, still add: `carrefour_search` with `query=<numeric product_id>` (stale `{query,limit}` schema) or `action=add` `product_id=` `qty=`, or `bf_cart retailer=carrefour`. Do not invent a local cart. `action=get|read|show|view` is **list**. If login fails, stop — no guest/virtual/unlogged cart. add takes `product_id` or `name` plus `qty`. Add binds the MAF delivery store from the saved Carrefour location; `error_code=needs_delivery_slot` means list the cart and retry. `clear` (also `create`/`empty`/`new`) empties the official cart. Checkout stays on the Carrefour website. Server MCP names: `carrefour_cart` / `carrefour_status` / `carrefour_search` (some clients prefix `bring_fast___`; the server accepts both).
- If official cart cannot be read: `items=[]` and say **unread**. Do not reuse old items. `error_code=akamai_blocked` means this server's HTTP is blocked by Carrefour (Akamai) — login is still saved (`login_saved=true`). `error_code=litecart_http_error` means the official-site liteCart call returned HTTP 400/401 after retries — login is still saved; retry `bf_cart retailer=carrefour action=list`. Website XHR always sends a `userId` header (cookie `userId`, else storage/JWT/email); a missing-header 400 is not a dead end if harvest can fill it. `error_code=varnish_blocked` means Union Coop Magento HTTP was blocked by Fastly/Varnish — login is still saved. `error_code=cart_timeout` means the official cart did not answer in time; same: login is not missing. Akamai on Carrefour login does **not** mean the login is missing. When HTTP is blocked, list and add run as same-origin fetches in the official site browser with the saved login (not product-page clicks). Checkout stays on the Carrefour website. Never invent a local cart.
- Do not call `placeOrder` unless the user explicitly asks to place the order. `ccod` is Magento card-on-delivery, not entering a card in Bring Fast.
- Payment stays on the supermarket site (or at delivery for ccod/cashondelivery).
- MMI and African + Eastern: License DXB login + search. No cart.
- Delivery note when relevant: Leave with security. Do not ring, call, or leave at the door.

## X (Twitter)

Same MCP URL (`/mcp` on Domvs). Do **not** use Cursor's X connector — it is not this user's developer app and cannot create posts.

Credentials live on the Domvs host: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` (user-context OAuth 1.0a). Optional `X_BEARER_TOKEN` for app-only reads. Never ask the user to paste keys. If a tool returns `x_credentials_missing` or `x_user_context_required`, tell the operator to set those env vars on Domvs.

Default account to **read** is **@ilTrumpista** when `username` is omitted. Pass `username=me` for the authenticated developer-app user.

| User asks | Call |
|---|---|
| Who am I on X / this app's X user | `x_me` |
| Profile of @ilTrumpista (or another handle) | `x_user_by_username` (`username` optional, default `ilTrumpista`) |
| Timeline / recent posts | `x_user_posts` |
| Mentions of that user | `x_mentions` |
| Search recent posts (last 7 days) | `x_search` `query=` e.g. `from:ilTrumpista` |
| Post / tweet / reply | `x_post` — **WRITE**, creates a live tweet. `text` required. Optional `reply_to` (tweet id). Only when the user explicitly asked to post. |

`x_post` publishes as the authenticated X user on this host. Confirm the exact text before calling it.

## Never

- Invent products, prices, invoices, or a virtual cart.
- Report `awaiting_official_payment`.
- Mix this user's data with anyone else.
- Guide the user through UI checklists when a tool can answer.
- Claim whoami/stores include recent orders. They do not.
- Call `x_post` unless the user asked to publish a tweet. Grocery tools never tweet.
