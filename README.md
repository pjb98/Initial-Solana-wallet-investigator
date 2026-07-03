# Solana Developer Wallet Investigator

FastAPI service for GPT Actions that traces a developer wallet, follows related token flows, and returns deterministic evidence-backed analysis.

## Endpoints

- `POST /analyze-developer-wallet`
- `GET /health`

## Environment

- `HELIUS_API_KEY` - required for on-chain RPC access
- `ACTION_SECRET` - bearer secret used by the GPT Action
- `DISCORD_WEBHOOK_URL` - optional webhook for alerts when a qualifying report completes
- `TROJAN_TERMINAL_URL_TEMPLATE` - optional hyperlink template included in Discord alerts, defaults to `https://trojan.com/terminal?token={mint}`
- `INVESTIGATOR_CACHE_PATH` - optional SQLite cache path, defaults to `/tmp/solana-investigator-cache.sqlite`
- `INVESTIGATOR_MAX_PAGES` - cap on signature pages per wallet, default `8`
- `INVESTIGATOR_LAUNCH_WINDOW_HOURS` - how far back to look around inferred launch time, default `72`

Example local setup:

```bash
cp .env.example .env
export HELIUS_API_KEY=...
export ACTION_SECRET=...
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export HELIUS_API_KEY=...
export ACTION_SECRET=...
uvicorn app.main:app --reload --port 8080
```

## GPT Action auth

Use a bearer token header when configuring the Action:

```text
Authorization: Bearer <ACTION_SECRET>
```

The Helius key stays server-side in `.env` or process environment variables and is never included in GPT instructions.

## Suggested GPT instructions

Use this as the GPT-level instruction text so reports stay consistent:

```text
You are a Solana wallet investigator. Use the analyzeDeveloperWallet action for wallet/mint traces.

Rules:
- Treat attribution as unverified unless the API output supports it with evidence.
- Do not claim a transfer is a sale unless the API output shows a sale or proceeds event.
- Prefer the API result over memory or browsing.
- Summarize only the facts in the response.
- If the result is not_found or confidence is low, say the evidence is insufficient.

Report format:
1. One-sentence conclusion.
2. Scope analyzed.
3. Key findings in bullets.
4. Side-wallet or proceeds chain, if any.
5. Final attribution status and confidence.
6. Do not speculate beyond the returned evidence.
```

## OpenAPI

Use [`openapi.yaml`](./openapi.yaml) directly in the GPT Actions editor.
For the GPT Action, use only `POST /analyze-developer-wallet`.
Paste the raw YAML content, not a fenced code block.

## Render deploy

1. Push this folder to a GitHub repo.
2. Sign in to Render and create a new `Web Service` from that repo.
3. Choose `Docker` as the environment.
4. Leave the start command as the Dockerfile command.
5. Add environment variables:
   - `HELIUS_API_KEY`
   - `ACTION_SECRET`
6. Deploy the service.
7. Copy the service URL Render assigns, such as `https://your-service.onrender.com`.
8. Replace the `servers.url` value in [`openapi.yaml`](./openapi.yaml) with that URL if it differs.
9. Paste the updated schema into your GPT Action.
10. Set the Action authentication header to `Authorization: Bearer <ACTION_SECRET>`.

If you want a private test first, use the local CLI:

```bash
cd /root/solana-wallet-investigator
./investigate.sh <mint> <developer-wallet>
```

## Automated utility-project watch

This repo now includes a live watcher that:

- listens for new pump.fun token launches
- reads token metadata and social links
- crawls the project website for docs, GitHub, and other useful links
- prefers Nitter RSS mirrors for X/Twitter accounts and merges mirror feeds to reduce stale-cache noise
- scores whether the token looks like a utility project
- runs the full wallet-trace report only on candidates

Run it locally:

```bash
cd /root/solana-wallet-investigator
python auto_utility_scan.py --watch
```

Optional environment variables:

- `PUMPPORTAL_API_KEY` - optional, for funded PumpPortal trade streaming
- `DISCORD_WEBHOOK_URL` - optional Discord webhook for completed utility/infra alerts
- `TROJAN_TERMINAL_URL_TEMPLATE` - optional Discord alert link template
- `UTILITY_SCORE_THRESHOLD` - default `6`
- `UTILITY_CRAWL_PAGES` - default `8`
- `UTILITY_CRAWL_DEPTH` - default `1`
- `UTILITY_ANALYSIS_DEPTH` - default `3`
- `UTILITY_ANALYSIS_PAGES` - default `12`

Output:

- `reports/latest.md`
- `reports/latest.json`
- `reports/<token>.md`
- `reports/<token>.json`
- `reports/transactions.csv`
- `reports/wallet_graph.csv`

Discord alerts are sent only after the watcher completes a qualifying report. Skips and exclusions do not trigger notifications.

If you want a single command:

```bash
./run_utility_watch.sh
```

## Dashboard

The watcher stores scraped tokens in `data/utility_watch.sqlite`. A read-only dashboard is now available from the same FastAPI app.

VPS service:

```bash
systemctl status solana-wallet-investigator-dashboard.service
```

Local URL on the VPS:

```text
http://127.0.0.1:8010/dashboard
```

If the VPS port is reachable from your browser, use:

```text
http://<your-vps-ip>:8010/dashboard
```

The page is public, but the data APIs use the same bearer token as the GPT Action. Paste that token into the dashboard login box.

Useful endpoints:

- `/dashboard`
- `/dashboard/api/tokens`
- `/dashboard/api/tokens/{mint}`
- `/reports/<filename>` for generated report files
