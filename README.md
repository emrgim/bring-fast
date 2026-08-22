# Fast Bring

Multi-user grocery MCP + dashboard for the UAE.

Each person has a Fast Bring account and only their own supermarket logins. Grok connects with OAuth (Dynamic Client Registration) — users sign in with the same Fast Bring email/password. No Client ID / secret to paste.

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

## Grok

Custom connector URL: `https://<your-host>/mcp`

Grok discovers OAuth and opens the Fast Bring login. Friends register their own Fast Bring account; they never see another user’s stores.

Official checkout stays on each supermarket site.

## License

MIT
