"""FastAPI application for developer wallet tracing."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .dashboard import get_token, list_tokens
from .cache import CacheStore
from .config import BASE_DIR, SETTINGS
from .helius import HeliusClient
from .models import AnalyzeRequest, HealthResponse, InvestigationCreateResponse, InvestigationRecord
from .security import require_action_secret
from .tracer import ACTION_VERSION, TraceEngine


app = FastAPI(
    title="Solana Developer Wallet Investigator",
    version=ACTION_VERSION,
    description="Deterministic Solana wallet tracing for GPT Actions.",
)

REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Solana Token Scrape Dashboard</title>
  <style>
    :root {
      --bg: #08111f;
      --panel: rgba(12, 21, 38, 0.92);
      --panel-2: rgba(18, 30, 51, 0.94);
      --text: #eaf2ff;
      --muted: #93a7c7;
      --accent: #7ef1c7;
      --accent-2: #62a8ff;
      --border: rgba(255,255,255,0.09);
      --danger: #ff7c8d;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(98,168,255,0.24), transparent 32%),
        radial-gradient(circle at 80% 10%, rgba(126,241,199,0.17), transparent 24%),
        linear-gradient(180deg, #06101d 0%, #091325 100%);
      min-height: 100vh;
    }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 32px 20px 56px; }
    .hero {
      display: flex; justify-content: space-between; gap: 20px; align-items: end;
      padding: 22px 24px; border: 1px solid var(--border); border-radius: 22px;
      background: linear-gradient(180deg, rgba(12,21,38,0.96), rgba(10,18,32,0.92));
      box-shadow: 0 20px 60px rgba(0,0,0,0.28);
      margin-bottom: 18px;
    }
    h1 { margin: 0 0 6px; font-size: 30px; letter-spacing: -0.03em; }
    .sub { color: var(--muted); max-width: 720px; line-height: 1.5; }
    .pill {
      display: inline-flex; gap: 8px; align-items: center; padding: 8px 12px;
      border-radius: 999px; border: 1px solid var(--border); color: var(--muted);
      background: rgba(255,255,255,0.03); font-size: 13px;
    }
    .grid { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 18px; }
    .panel {
      border: 1px solid var(--border); border-radius: 18px; background: var(--panel);
      box-shadow: 0 20px 50px rgba(0,0,0,0.18); overflow: hidden;
    }
    .panel-head {
      display: flex; justify-content: space-between; gap: 14px; align-items: center;
      padding: 16px 18px; border-bottom: 1px solid var(--border); background: rgba(255,255,255,0.02);
    }
    .panel-head h2 { margin: 0; font-size: 17px; }
    .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    input, button {
      border-radius: 12px; border: 1px solid var(--border); background: rgba(255,255,255,0.04);
      color: var(--text); padding: 10px 12px; font: inherit;
    }
    input { min-width: 240px; }
    button { cursor: pointer; }
    button.primary { background: linear-gradient(135deg, var(--accent-2), var(--accent)); color: #07111d; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 12px 18px; border-bottom: 1px solid rgba(255,255,255,0.06); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .status, .verdict {
      display: inline-flex; align-items: center; padding: 6px 10px; border-radius: 999px;
      background: rgba(255,255,255,0.05); border: 1px solid var(--border); font-size: 12px;
    }
    .ok { color: var(--accent); }
    .warn { color: #ffcc7f; }
    .bad { color: var(--danger); }
    .muted { color: var(--muted); }
    .side { padding: 18px; }
    .card {
      background: var(--panel-2); border: 1px solid var(--border); border-radius: 16px; padding: 14px 16px; margin-bottom: 14px;
    }
    .kv { display: grid; grid-template-columns: 120px 1fr; gap: 8px 12px; font-size: 14px; }
    .kv div:nth-child(odd) { color: var(--muted); }
    a { color: var(--accent-2); text-decoration: none; }
    pre {
      white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.2);
      border-radius: 14px; padding: 14px; border: 1px solid var(--border); overflow: auto;
    }
    .error { color: var(--danger); }
    @media (max-width: 980px) { .grid { grid-template-columns: 1fr; } input { min-width: 180px; } }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div>
        <div class="pill">Live scrape dashboard</div>
        <h1>Solana Token Research Dashboard</h1>
        <div class="sub">Browse the tokens the watcher has seen, inspect the utility score and social links, and open the generated report for the full evidence trail.</div>
      </div>
      <div class="pill" id="status-pill">Loading</div>
    </div>

    <div class="grid">
      <div class="panel">
        <div class="panel-head">
          <h2>Scraped Tokens</h2>
          <div class="controls">
            <input id="token" placeholder="Bearer token for dashboard API" type="password" />
            <button class="primary" id="load">Load</button>
          </div>
        </div>
        <div id="table-wrap"></div>
      </div>
      <div class="panel side">
        <div class="card">
          <div class="kv">
            <div>API</div><div><code>/dashboard/api/tokens</code></div>
            <div>Detail</div><div><code>/dashboard/api/tokens/{mint}</code></div>
            <div>Reports</div><div><code>/reports/latest.md</code> and per-token outputs</div>
          </div>
        </div>
        <div class="card">
          <div class="muted" style="margin-bottom:10px;">Selected token</div>
          <div id="detail-empty" class="muted">Click a token row to view details.</div>
          <div id="detail" style="display:none;">
            <div class="kv" id="detail-kv"></div>
            <div style="margin-top:12px;" id="detail-links"></div>
            <div style="margin-top:12px;" id="detail-report"></div>
          </div>
        </div>
        <div class="card">
          <div class="muted" style="margin-bottom:8px;">Notes</div>
          <div class="muted">Use the same bearer token you configured for the GPT Action. The dashboard itself is public, but the data APIs require authorization.</div>
          <div class="muted" style="margin-top:8px;">Only completed utility candidates are stored here. Skipped meme launches are no longer inserted into the dashboard table.</div>
        </div>
      </div>
    </div>
  </div>

  <script>
    const state = { token: localStorage.getItem('dashboardToken') || '', rows: [], detail: null };
    const $ = (id) => document.getElementById(id);
    $('token').value = state.token;

    function authHeaders() {
      const raw = state.token.trim();
      if (!raw) return {};
      return { 'Authorization': raw.toLowerCase().startsWith('bearer ') ? raw : `Bearer ${raw}` };
    }

    function normalizeTokenInput(value) {
      return String(value || '').trim().replace(/^Bearer\\s+/i, '').trim();
    }

    function esc(v) {
      return String(v ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function badge(value) {
      const cls = value === 'reported' ? 'ok' : value === 'failed' ? 'bad' : value === 'scored' ? 'warn' : '';
      return `<span class="status ${cls}">${esc(value || 'unknown')}</span>`;
    }

    function verdictBadge(value) {
      const cls = value === 'utility_candidate' || value === 'infra_candidate' ? 'ok' : value === 'possible_utility' ? 'warn' : value === 'contract_not_found' || value === 'tiktok_excluded' || value === 'meme_candidate' ? 'bad' : '';
      return `<span class="verdict ${cls}">${esc(value || 'unclear')}</span>`;
    }

    function dexscreenerUrl(mint) {
      return `https://dexscreener.com/solana/${encodeURIComponent(mint)}`;
    }

    async function loadRows() {
      state.token = normalizeTokenInput($('token').value);
      $('token').value = state.token;
      localStorage.setItem('dashboardToken', state.token);
      if (!state.token) {
        $('status-pill').textContent = 'Enter bearer token';
        $('table-wrap').innerHTML = '<div class="side muted">Paste your dashboard bearer token and click Load.</div>';
        return;
      }
      $('status-pill').textContent = 'Loading';
      try {
        const res = await fetch('/dashboard/api/tokens?limit=200', { headers: authHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        state.rows = await res.json();
        renderTable();
        $('status-pill').textContent = `${state.rows.length} tokens`;
      } catch (err) {
        $('status-pill').textContent = 'Auth or fetch error';
        $('table-wrap').innerHTML = `<div class="side error">${esc(err.message)}</div>`;
      }
    }

    function renderTable() {
      const rows = state.rows.map((row) => `
        <tr data-mint="${esc(row.mint)}">
          <td>
            <strong><a href="${esc(dexscreenerUrl(row.mint))}" target="_blank">${esc(row.symbol || row.name || row.mint.slice(0,8))}</a></strong>
            <div class="muted"><a href="${esc(dexscreenerUrl(row.mint))}" target="_blank">${esc(row.mint)}</a></div>
          </td>
          <td>${badge(row.status)}</td>
          <td>${verdictBadge(row.verdict)}</td>
          <td>${esc(row.score ?? '')}</td>
          <td>${esc(row.discovered_at || '')}</td>
          <td>${esc(row.completed_at || '')}</td>
          <td>${row.contract_found ? `<span class="ok">found</span>` : `<span class="bad">missing</span>`}</td>
          <td>${row.report_url ? `<a href="${esc(row.report_url)}" target="_blank">report</a>` : '<span class="muted">none</span>'}</td>
        </tr>
      `).join('');
      $('table-wrap').innerHTML = `
        <table>
          <thead>
            <tr>
              <th>Token</th>
              <th>Status</th>
              <th>Verdict</th>
              <th>Score</th>
              <th>Discovered</th>
              <th>Completed</th>
              <th>Contract</th>
              <th>Report</th>
            </tr>
          </thead>
          <tbody>${rows || '<tr><td colspan="8" class="muted">No scraped tokens yet.</td></tr>'}</tbody>
        </table>
      `;
      document.querySelectorAll('tr[data-mint]').forEach((tr) => tr.addEventListener('click', () => loadDetail(tr.dataset.mint)));
    }

    async function loadDetail(mint) {
      try {
        const res = await fetch(`/dashboard/api/tokens/${mint}`, { headers: authHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        state.detail = data;
        $('detail-empty').style.display = 'none';
        $('detail').style.display = 'block';
        $('detail-kv').innerHTML = `
          <div>Token</div><div><strong>${esc(data.symbol || data.name || data.mint)}</strong></div>
          <div>Mint</div><div><a href="${esc(dexscreenerUrl(data.mint))}" target="_blank"><code>${esc(data.mint)}</code></a></div>
          <div>Creator</div><div><code>${esc(data.creator || '')}</code></div>
          <div>Status</div><div>${badge(data.status)}</div>
          <div>Verdict</div><div>${verdictBadge(data.verdict)}</div>
          <div>Score</div><div>${esc(data.score ?? '')}</div>
          <div>Discovered</div><div>${esc(data.discovered_at || '')}</div>
          <div>Completed</div><div>${esc(data.completed_at || '')}</div>
          <div>Contract</div><div>${data.contract_found ? `<span class="ok">found</span> <span class="muted">${esc(data.contract_evidence || '')}</span>` : '<span class="bad">missing</span>'}</div>
          <div>URI</div><div><a href="${esc(data.uri || '#')}" target="_blank">${esc(data.uri || '')}</a></div>
        `;
        const links = [];
        if (data.website) links.push(`<a href="${esc(data.website)}" target="_blank">Website</a>`);
        if (data.twitter) links.push(`<a href="${esc(data.twitter)}" target="_blank">Twitter/X</a>`);
        if (data.telegram) links.push(`<a href="${esc(data.telegram)}" target="_blank">Telegram</a>`);
        if (Array.isArray(data.useful_links)) data.useful_links.forEach((u) => links.push(`<a href="${esc(u)}" target="_blank">${esc(u)}</a>`));
        $('detail-links').innerHTML = links.length ? `<div class="muted" style="margin-bottom:6px;">Useful links</div>${links.join('<br/>')}` : '';
        $('detail-report').innerHTML = data.report_text ? `<div class="muted" style="margin-bottom:6px;">Report preview</div><pre>${esc(data.report_text.slice(0, 6000))}</pre>` : '';
      } catch (err) {
        $('detail-empty').style.display = 'block';
        $('detail').style.display = 'none';
        $('detail-empty').innerHTML = `<span class="error">${esc(err.message)}</span>`;
      }
    }

    $('load').addEventListener('click', loadRows);
    if (state.token) {
      loadRows();
    } else {
      $('status-pill').textContent = 'Enter bearer token';
      $('table-wrap').innerHTML = '<div class="side muted">Paste your dashboard bearer token and click Load.</div>';
    }
  </script>
</body>
</html>"""

cache = CacheStore(SETTINGS.cache_path)
helius = HeliusClient()
engine = TraceEngine(helius)


def _run_analysis(payload: AnalyzeRequest) -> dict[str, Any]:
    request_dict = payload.model_dump()
    cache_key = cache.cache_key(request_dict, ACTION_VERSION)
    cached = cache.get_analysis(cache_key)
    if cached is not None:
        return cached
    result = engine.analyze(request_dict)
    cache.put_analysis(cache_key, request_dict, result)
    return result


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="solana-developer-wallet-investigator", helius_configured=helius.configured)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD_HTML)


@app.get("/dashboard/api/tokens", dependencies=[Depends(require_action_secret)])
def dashboard_tokens(limit: int = Query(default=200, ge=1, le=1000)) -> list[dict[str, Any]]:
    return list_tokens(limit=limit)


@app.get("/dashboard/api/tokens/{mint}", dependencies=[Depends(require_action_secret)])
def dashboard_token_detail(mint: str) -> dict[str, Any] | None:
    token = get_token(mint)
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token not found")
    return token


@app.post("/analyze-developer-wallet", dependencies=[Depends(require_action_secret)])
async def analyze_developer_wallet(payload: AnalyzeRequest) -> dict[str, Any]:
    if not helius.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HELIUS_API_KEY is not configured",
        )
    return await asyncio.to_thread(_run_analysis, payload)


@app.post("/investigations", response_model=InvestigationCreateResponse, dependencies=[Depends(require_action_secret)])
async def create_investigation(payload: AnalyzeRequest) -> InvestigationCreateResponse:
    if not helius.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HELIUS_API_KEY is not configured",
        )
    investigation_id = str(uuid.uuid4())
    cache.create_investigation(investigation_id, payload.model_dump())
    cache.update_investigation(investigation_id, status="running")

    def _worker() -> None:
        try:
            result = _run_analysis(payload)
            cache.update_investigation(investigation_id, status="completed", result=result)
        except Exception as exc:  # pragma: no cover - background safety net
            cache.update_investigation(investigation_id, status="failed", error=str(exc))

    asyncio.get_running_loop().run_in_executor(None, _worker)
    return InvestigationCreateResponse(investigation_id=investigation_id, status="running")


@app.get("/investigations/{investigation_id}", response_model=InvestigationRecord, dependencies=[Depends(require_action_secret)])
async def get_investigation(investigation_id: str) -> InvestigationRecord:
    record = cache.get_investigation(investigation_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="investigation not found")
    return InvestigationRecord(
        investigation_id=record["investigation_id"],
        status=record["status"],
        request=AnalyzeRequest(**record["request"]),
        result=record["result"],
        error=record["error"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )
