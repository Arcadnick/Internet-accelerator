# VPN Panel + Relay

Universal VPN service: one **main server** hosts both the admin panel and an
XRay-core **relay**. Clients connect with a single Happ subscription URL,
pick a country in-app, and traffic is tunneled through the main server to
the chosen **exit node** in that country.

```
Happ (client) ──VLESS+Reality──▶ Main server (Panel + XRay relay)
                                              │
                                              │ Trojan over TLS (only Panel IP allowed)
                                              ▼
                                       Exit node (DE / NL / US / …)
                                              │
                                              ▼
                                          internet
```

- Exit node IPs are **never exposed** to clients.
- Adding a new country = one form in the admin panel + 90 seconds of SSH bootstrap.
- Per-user traffic accounting and time/volume limits are enforced on the relay.

## Quick start (development)

```bash
cd panel
cp .env.example .env
# edit .env: set ADMIN_PASSWORD, JWT_SECRET, PANEL_HOST
docker compose up -d --build
```

Panel is reachable at <http://localhost:8000>. Default credentials match `.env`.

For a real deployment the panel itself needs an XRay process listening on
the relay ports; see *Production deployment* below.

## Production deployment

Run on a fresh Ubuntu 22.04/24.04 or Debian 12 box with a public IP.

1. **Install XRay-core on the host** (relay role) and the auto-reload watcher:

   ```bash
   sudo bash panel/scripts/install_panel.sh
   ```

   This prints a Reality keypair. Copy `PANEL_REALITY_PRIVATE_KEY` and
   `PANEL_REALITY_PUBLIC_KEY` into `panel/.env`.

2. **Start the panel stack**:

   ```bash
   cd panel
   docker compose up -d --build
   ```

   The panel container writes XRay configs to `/usr/local/etc/xray/config.json`
   via a bind-mounted volume; the host-side `xray-watcher.service` detects
   the change, validates with `xray test`, and restarts XRay.

   > **Note**: in production, replace the `xray_config` named volume in
   > `docker-compose.yml` with a bind mount: `- /usr/local/etc/xray:/xray`.

3. **Expose ports**:
   - 8000 → behind a reverse proxy with TLS (Caddy, nginx).
   - 10001-10999 → directly to the internet (these are the per-country
     relay inbounds; clients connect here).

4. **Add an exit node** via the admin API:

   ```bash
   curl -X POST http://localhost:8000/api/admin/provision/node \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "country_code": "DE",
       "label": "Frankfurt",
       "host": "de1.example.com",
       "ssh_user": "root",
       "ssh_password": "the-root-password",
       "admin_ssh_key": "ssh-ed25519 AAAA... admin@laptop"
     }'
   ```

   The response is an SSE stream of bootstrap logs ending with a `done` event.

5. **Create a plan and a subscription** for a test user, copy the
   subscription URL from `/api/me/subscriptions`, and import into Happ.

## Architecture

See [the design plan](docs/plan.md) (or `~/.claude/plans/jiggly-tickling-moler.md`
in the original workspace) for the full design rationale, including the
trade-offs of the single-entry relay topology.

## Repository layout

```
panel/
├── app/
│   ├── api/                 # FastAPI routers (admin + user + subscription)
│   ├── models/              # SQLAlchemy ORM
│   ├── schemas/             # Pydantic request/response models
│   ├── services/
│   │   ├── relay_config.py        # generates XRay config from DB state
│   │   ├── xray_local.py          # atomic config writer (triggers watcher)
│   │   ├── provisioner.py         # AsyncSSH bootstrap of exit nodes
│   │   ├── subscription_builder.py# vless:// + sing-box JSON
│   │   ├── traffic_collector.py   # pulls per-user stats from XRay gRPC
│   │   └── billing.py             # expires subs at time/traffic limit
│   ├── workers/             # ARQ cron jobs (traffic + billing)
│   ├── templates/           # Jinja2 (user dashboard)
│   ├── web.py               # cookie-session HTML routes
│   └── main.py              # FastAPI entrypoint + lifespan seed
├── alembic/                 # schema migrations
├── scripts/
│   ├── install_panel.sh     # one-shot host setup for Panel XRay
│   └── bootstrap_node.sh    # uploaded to each exit node by the provisioner
└── docker-compose.yml
```

## Roadmap

- **Phase 5** — payment integration (Stripe / Tribute / crypto). The
  `payments` table is intentionally not present yet; add when the first
  provider is chosen.
- **HA for the main server** — currently single-node; pair with snapshot +
  fast redeploy. Active-active relay is a separate epic.
- **Multi-admin / reseller accounts**.
