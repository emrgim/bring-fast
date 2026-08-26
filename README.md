# Bring Fast

Multi-user grocery MCP + dashboard for the UAE.

Each person has a Bring Fast account and only their own supermarket logins. Grok connects with OAuth (Dynamic Client Registration) — users sign in with the same Bring Fast email/password. No Client ID / secret to paste.

## Login

One form, one step. Email plus password signs you in, and on first use that same
form creates the account — there is no separate registration page to find.

- The session cookie lasts 30 days (`BRINGFAST_SESSION_DAYS`), and is `Secure`
  when `BRINGFAST_PUBLIC_URL` is https.
- A wrong password keeps the email you typed, and `?next=` brings you back to the
  page you were heading for.
- When Grok asks for access and you are already signed in, Bring Fast hands the
  code straight back with no extra screen. When you are not, the authorize page
  signs you in — or creates the account — and continues the same flow.
- Connectors get a `refresh_token`, so a running connector is never dropped back
  to a login screen.
- Authorization codes only go to a `redirect_uri` the client registered.

Store logins live inside each store's own page. Bring Fast reuses a live
supermarket session when it belongs to you, and signs in again when it does not,
so a shared browser profile never mixes two people's carts. Use **Check login**
on the store's page to test the saved credentials.

## Offline

The dashboard is a full offline app. Every page you open is kept on the device,
so the same page opens again with no network — a page you never visited shows a
short offline screen instead of a browser error.

- **Online**: pages come from the server, so a change shows up at once.
- **Offline**: the saved copy is served and the app retries every 10 minutes,
  refreshing everything the moment the network answers again. A chip in the
  header says `Offline · saved 09:12 · retry 9:47`, so both the countdown and
  the age of what you are reading are on screen.
- Dashboard, Purchases and Stores are saved in the background once a page has
  finished loading — one tab at a time, so the page you are reading keeps the
  network to itself.
- Product shots are kept with the page that shows them, and the shell — offline
  screen, icons and font — is saved on install. A saved page looks like the app,
  not like a stylesheet that never arrived.
- Sending a form with no network says **Not saved** and keeps you in the app
  instead of handing you a browser error. Nothing is queued behind your back.
- A page reached by a redirect is never saved under the address that was asked
  for, so an expired session cannot leave a login form named “Dashboard”.
- Signing out wipes the saved pages, so a shared device never shows the next
  person your receipts.

## Installed app

`display: standalone`, so the installed app has no browser chrome. Chrome and
Edge get the **Install** button in the header; Safari has no install prompt to
offer, so iOS is shown where the Share sheet's *Add to Home Screen* lives — once,
and “Not now” sticks.

- Installed, the app cannot be pinched. Zooming out past scale 1 parks the
  sticky header and the bottom dock off the screen until the next scroll
  settles, so the app takes the pan and refuses the pinch — on WebKit the
  gesture itself is turned down, since Safari keeps its pinch out of reach of
  the viewport rules. In a browser tab zoom is left alone: there a page is
  still a page. A receipt is the one screen that stays pinchable installed,
  because it is a scan of paper and the small print is the reason to open it.
- Installed, the app does not pull down. There is nothing behind it to reveal, so
  a bounce at the top only ever dragged the whole app off the screen to show a
  strip of nothing above its own header. In a browser tab the pull is left alone:
  there it is the browser's, and it reloads.
- No screen draws a scrollbar. A track down the side is furniture the browser
  lends a document, and this is an app. Only the bar is gone — the page, the
  spend bars and the desk table all still scroll, and reaching the end of one of
  them is the end of it, not the start of a bounce that moves the page behind.
  The bars used to reserve a gutter to hold their own 6px track; without it the
  spend card is the height of its bars.
- Switching tabs never waits on a shelf. **Buys** draws its board — spend,
  receipts, the bars — with the first two dozen products, and the rest of the
  shelf is not in the page at all: it is asked for a batch at a time once the
  board is on screen. So an account with hundreds of products costs the same to
  open as an account with a dozen. A line under the list says how many products
  are still coming and disappears at the end; if a batch never arrives it becomes
  the way to ask for the rest again.
  - A batch is asked for once the pictures already on the wire have mostly
    landed, and a shop that never answers holds the shelf up for a second and a
    half, not for ever. Scrolling to the end skips the wait.
  - A batch carries both shapes a product takes — the phone card and the desk
    row — and only the one the layout is showing is put into the page, so a phone
    asks for each picture once. Turning the screen catches the other one up.
  - Tapping a link stops the shelf where it is and drops the batch in flight, so
    the next tab's own request is never queued behind product shots. Leaving the
    app pauses it; coming back picks it up.
  - Every batch is saved on the device like a page, and warming a tab follows
    the first few batches of its shelf, one after another. Offline the shelf
    reads as far as it was saved.
- Nothing waits for a scroll. Every picture is asked for on its own, without an
  interaction, and carries its own width and height, so a slow shop CDN cannot
  shove the rows around. Until a shot lands its box sweeps in place; a shot the
  shop never sends leaves the product's letter behind rather than a broken box.
  Batches that arrive after the page ask at low priority, so they never outrank
  what the reader opened next.
- The app's own markup goes out compressed — a page of a thousand bars is the
  same forty characters over and over — while fonts, logos and receipt scans are
  handed on untouched, being compressed formats already.
- The font is served by Bring Fast itself. No font CDN means nothing
  third-party blocks the first paint, and the app reads the same offline.
- Safe areas on all four sides, so a notch in landscape and the iPhone home bar
  never sit on top of a row. Date fields are 16px on a phone, because anything
  smaller makes iOS zoom in on focus and never zoom back out.
- The phone header keeps **Sign out** and drops only the address; the tabs move
  to the bottom dock.
- Fonts are immutable for a year, logos for a day, and a page is always
  revalidated — money figures are never read from a stale cache.

## A long shelf

An account with hundreds of products used to send every row twice — once as a
phone card, once as a desktop table row — and ask for every shop picture at
load. On a throttled phone that was 517 image requests and a page that took ten
seconds to finish, which is why a tap sat there before anything moved.

- The buys tab arrives with the first screenful drawn. The rest travels as inert
  text the browser only tokenises: no row is laid out and no picture is asked
  for until it is poured into the page.
- Pouring starts once the page has finished loading and continues a batch at a
  time, while the browser is idle and while the shots already asked for are
  still arriving. Scrolling to the end skips the wait; a stalled shop cannot
  stop the list; a line under the shelf says how many products are still coming.
- Only the list the layout is showing is filled — the phone card list or the
  desktop table, never both — so the same products are no longer fetched twice.
- Every figure is still the whole shelf. The sort, the totals, the receipt count
  and the period average are computed over all products on the server, exactly
  as before; only the drawing is progressive.
- Warming a page in the background saves the shots that page draws, three at a
  time, instead of every shot it names all at once. The rest are saved as the
  list fills them in, through the same cache.
- The buys tab carries the same compact bar as home: scroll past the average and
  it stays on a bar under the header, so the figure never leaves the screen.

On a phone throttled to a quarter of its speed on a 3 Mbps link, the tab now
answers in about 170 ms and finishes loading in about 400 ms, against ten
seconds before.

## Updates

**Update** in the header appears when `origin/main` is ahead. Pressing it takes
the page over: the update installs, the server restarts, and the page counts the
restart down instead of going blank or dying on a reload.

- The restart is deferred a couple of seconds (`BRINGFAST_RESTART_DELAY`) so the
  browser always receives the response the countdown is built from.
- The page reloads itself only once `/health` reports a new `boot` id — the new
  server, not a dead port. A slow restart keeps the page and offers a reload.
- Without a `fast-bring` service to restart, the update still lands and the page
  says the restart is yours to make.
- `deploy/fast-bring-update-check.timer` checks GitHub every 10 minutes; an
  online page also checks when it loads and whenever the network comes back.

## Stores

The **Stores** tab reads the stores out and changes none of them: a card per
store saying whether it is on and what it can do, which opens that store. The
switch, the login, the delivery address and **Check login** all live inside the
store's own page, so opening the tab never puts a password field on screen and a
stray tap cannot turn a store off.

What a store can do is stated next to the registry that decides it
(`db.RETAILERS`): a card shows a capability filled when it works and outlined
when it does not.

| | Search | Compare | Cart | Checkout | Receipts | Login |
| --- | --- | --- | --- | --- | --- | --- |
| [Grandiose](https://www.grandiose.ae/) | yes | yes | yes | yes | yes | yes |
| [Union Coop](https://www.unioncoop.ae/) | yes | yes | yes | yes | — | — |
| [Carrefour UAE](https://www.carrefouruae.com/mafuae/en) | yes | yes | — | — | yes | yes |
| [Waitrose UAE](https://www.waitrose.ae/en/) | yes | yes | — | — | — | yes |
| [Spinneys UAE](https://www.spinneys.com/en-ae/) | yes | yes | — | — | — | yes |
| [MMI](https://www.mmihomedelivery.ae/) | yes | yes | — | — | yes | yes |
| [African + Eastern](https://www.africaneasternonline.com/) | yes | yes | — | — | yes | yes |
| [Careem](https://www.careem.com/) | — | — | — | — | yes | — |

Search and price comparison read public catalog pages, so they go together and a
store with no catalog has neither. Cart and checkout are the Magento stores we
have tested. Receipts means there is a parser for that store's invoices, and
login means a saved store account, which is what receipts and baskets are read
with.

Careem is receipts only. It is food delivery, not a supermarket: the menu is per
restaurant and per hour, so there is no catalog to search and no shelf price to
compare a dish against. It is a store here so its emailed invoices have somewhere
to land, and it is left out of search, comparison and the price refresh rather
than kept in them answering errors. A Careem invoice names the restaurant it came
from, so purchases read `Careem · Zaroob` and not just `Careem`.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
BRINGFAST_HOST=0.0.0.0 BRINGFAST_PORT=8877 \
  BRINGFAST_PUBLIC_URL=https://your-public-host \
  .venv/bin/python -m bring_fast.app
```

Official store checkout (Playwright) is optional:

```bash
.venv/bin/pip install -e ".[checkout]"
.venv/bin/playwright install chromium
```

- Dashboard: `/`
- MCP: `/mcp`
- Health: `/health`
- OAuth: `/.well-known/oauth-authorization-server`, `/oauth/register`, `/oauth/authorize`, `/oauth/token`

`BRINGFAST_PUBLIC_URL` is what the server tells clients to call back on. Leave it unset behind a reverse proxy or tunnel that sends `X-Forwarded-Proto` / `X-Forwarded-Host` and the public URL is taken from the request. Set it explicitly only to a **public https origin**. A server that advertises `127.0.0.1` gives the connector nothing reachable to authenticate against.

If Grok hangs on “Checking authentication…” or reports “Connection failed”, diagnose the live URL:

```bash
.venv/bin/python -m bring_fast.doctor https://your-public-host/mcp
```

The first `FAIL` line is the reason the connector never finishes.

## Grok

Custom connector URL: `https://<your-host>/mcp`

Grok discovers OAuth and opens the Bring Fast login. Friends register their own Bring Fast account; they never see another user’s stores.

Official checkout stays on each supermarket site.

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

`tests/test_mcp_handshake.py` replays the OAuth and MCP handshake a client performs.
Login, refresh tokens, and per-user store sessions are covered in `tests/test_login.py`,
`tests/test_oauth.py`, and `tests/test_store_login.py`. The installed app is covered in
`tests/test_pwa.py`, `tests/test_offline_ui.py`, `tests/test_webapp_layout.py`, and
`tests/test_content_loading.py`. A long shelf and the bar that carries its figure
are covered in `tests/test_progressive_buys.py`, the shelf arriving a batch at a
time while the board is already on screen in `tests/test_shelf_loading.py`, and
the split between the store list and a store's own page in
`tests/test_store_credentials.py`.

## Docker

```bash
docker build -t bring-fast .
docker run --rm -p 8877:8877 \
  -e BRINGFAST_PUBLIC_URL=https://your-public-host \
  -v bringfast-data:/data \
  bring-fast
```

## License

MIT
