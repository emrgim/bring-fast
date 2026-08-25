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

Store logins live on the dashboard. Bring Fast reuses a live supermarket session
when it belongs to you, and signs in again when it does not, so a shared browser
profile never mixes two people's carts. Use **Check login** on a store card to
test the saved credentials.

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
  finished loading, so the first time the network drops there is already
  something to read on all three.
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

- Nothing is lazy-loaded. Every picture is requested with the page and carries
  its own width and height, so a slow shop CDN cannot shove the rows around.
- The font is served by Bring Fast itself. No font CDN means nothing
  third-party blocks the first paint, and the app reads the same offline.
- Safe areas on all four sides, so a notch in landscape and the iPhone home bar
  never sit on top of a row. Date fields are 16px on a phone, because anything
  smaller makes iOS zoom in on focus and never zoom back out.
- The phone header keeps **Sign out** and drops only the address; the tabs move
  to the bottom dock.
- Fonts are immutable for a year, logos for a day, and a page is always
  revalidated — money figures are never read from a stale cache.

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

- [Carrefour UAE](https://www.carrefouruae.com/mafuae/en)
- [Grandiose](https://www.grandiose.ae/)
- [Waitrose UAE](https://www.waitrose.ae/en/)
- [Spinneys UAE](https://www.spinneys.com/en-ae/)

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
`tests/test_content_loading.py`.

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
