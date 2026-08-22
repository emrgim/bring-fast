# Bring Fast

Multi-user grocery MCP + dashboard for the UAE.

Each person has a Bring Fast account and only their own supermarket logins. Grok connects with OAuth (Dynamic Client Registration) — users sign in with the same Bring Fast email/password. No Client ID / secret to paste.

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

`BRINGFAST_PUBLIC_URL` is optional: behind a reverse proxy such as Tailscale Funnel the server derives its public origin from the incoming request, so the OAuth metadata always matches the URL the client actually called.

## Grok

Custom connector URL: `https://<your-host>/mcp`

Grok discovers OAuth and opens the Bring Fast login. Friends register their own Bring Fast account; they never see another user’s stores.

Official checkout stays on each supermarket site.

## Grok cannot connect

Run the doctor from any machine — it replays every step Grok performs, and the
first `FAIL` line is the reason the connector hangs on “Checking authentication…”:

```bash
.venv/bin/python -m bring_fast.doctor https://<your-host>/mcp
# add --token <mcp token> to also exercise the authenticated MCP calls
```

The usual causes:

- **TLS handshake times out** — the URL is not actually served. Tailscale only
  publishes the DNS record; check `tailscale status`, then `tailscale funnel status`,
  and confirm Bring Fast answers locally on its port. Start Funnel with
  `tailscale funnel --bg 8877` so it survives the shell that started it.
- **Issuer mismatch / localhost endpoints** — `BRINGFAST_PUBLIC_URL` is stale.
  Unset it, or set it to the exact public URL.
- **Connector URL is not HTTPS** — Grok only accepts `https://`.

## License

MIT
