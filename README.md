# Solana Developer Wallet Investigator

FastAPI service for GPT Actions that traces a developer wallet, follows related token flows, and returns deterministic evidence-backed analysis.

## Endpoints

- `POST /analyze-developer-wallet`
- `GET /health`

## Environment

- `HELIUS_API_KEY` - required for on-chain RPC access
- `ACTION_SECRET` - bearer secret used by the GPT Action
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

## OpenAPI

Use [`openapi.yaml`](./openapi.yaml) directly in the GPT Actions editor.
For the GPT Action, use only `POST /analyze-developer-wallet`.

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
