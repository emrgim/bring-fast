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

## Stores

- [Carrefour UAE](https://www.carrefouruae.com/mafuae/en)
- [Grandiose](https://www.grandiose.ae/)
- [Waitrose UAE](https://www.waitrose.ae/en/)
- [Spinneys UAE](https://www.spinneys.com/en-ae/)

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
BRINGFAST_HOST=127.0.0.1 BRINGFAST_PORT=8765 \
  BRINGFAST_PUBLIC_URL=https://your-public-host \
  .venv/bin/python -m bring_fast.app
```

- Dashboard: `/`
- MCP: `/mcp`
- Health: `/health`
- OAuth: `/.well-known/oauth-authorization-server`, `/oauth/register`, `/oauth/authorize`, `/oauth/token`

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## Grok

Custom connector URL: `https://<your-host>/mcp`

Grok discovers OAuth and opens the Bring Fast login. Friends register their own Bring Fast account; they never see another user’s stores.

Official checkout stays on each supermarket site.

## License

MIT
