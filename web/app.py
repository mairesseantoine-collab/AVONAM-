"""Interface web minimale pour visualiser le moteur de trading dans un
navigateur : formulaire de paramètres, backtest exécuté côté serveur,
courbe d'équity et statistiques renvoyées en JSON et affichées côté client
en JavaScript natif (pas de dépendance front-end à builder).

⚠️ Ce serveur expose le moteur de trading (`avonam/`) et, en lecture seule,
les données de marché PUBLIQUES de Kraken (`broker/kraken/market_data.py`,
aucune clé API, aucun ordre) pour faire du paper trading sur des données
réelles. Il n'expose en revanche JAMAIS :
    - le module bancaire (`bank/`) : déclenche des consentements PSD2
      réels, ne doit jamais être accessible depuis un serveur public sans
      authentification utilisateur, HTTPS, et le statut TPP requis (voir
      le README, section « Intégration bancaire PSD2 ») ;
    - les endpoints privés de `broker/kraken/` (solde, passage d'ordres,
      `LiveExecutionBridge`) : aucune clé API Kraken n'est lue par ce
      fichier, et aucun ordre, dry-run ou réel, ne peut être déclenché
      depuis cette interface publique. Ne les ajoutez pas ici sans avoir
      relu la section « Exécution réelle crypto » du README et sans avoir
      d'abord mis en place une authentification.

Lancer en local :
    python -m uvicorn web.app:app --reload

Lancer en conteneur : voir Dockerfile / DEPLOY.md à la racine du projet.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from avonam.backtest.engine import BacktestEngine
from avonam.data.loader import load_csv
from avonam.risk.manager import RiskManager
from avonam.strategy.sma_crossover import SMACrossoverStrategy
from broker.kraken.market_data import fetch_ohlc_dataframe

app = FastAPI(title="AVONAM — Tableau de bord de trading (simulation)")

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sample" / "DEMO.csv"

# Paires Kraken autorisées pour le paper trading sur données réelles.
# Endpoint PUBLIC uniquement (aucune clé API, aucun ordre, aucun risque) —
# voir broker/kraken/market_data.py et l'avertissement en tête de ce fichier.
KRAKEN_PAIRS = {"XBTEUR", "ETHEUR"}


@app.get("/api/backtest")
def run_backtest(
    source: str = Query("demo"),
    pair: str = Query("XBTEUR"),
    fast_period: int = Query(20, ge=1),
    slow_period: int = Query(50, ge=2),
    initial_capital: float = Query(10_000, gt=0),
    risk_per_trade_pct: float = Query(1.0, gt=0),
    stop_loss_pct: float = Query(2.0, gt=0),
    take_profit_pct: float = Query(4.0, gt=0),
    max_drawdown_pct: float = Query(20.0, gt=0),
) -> dict:
    if fast_period >= slow_period:
        return {"error": "fast_period doit être strictement inférieur à slow_period"}

    if source == "kraken":
        if pair not in KRAKEN_PAIRS:
            return {"error": f"Paire non autorisée : {pair}"}
        try:
            # Endpoint public Kraken : aucune clé API, aucune écriture,
            # uniquement des bougies OHLC en lecture seule.
            data = fetch_ohlc_dataframe(pair, interval_minutes=60)
        except Exception as exc:  # réseau Kraken indisponible, etc.
            return {"error": f"Impossible de récupérer les données Kraken : {exc}"}
    else:
        data = load_csv(DATA_PATH)

    strategy = SMACrossoverStrategy(fast_period=fast_period, slow_period=slow_period)
    risk_manager = RiskManager(
        initial_capital=initial_capital,
        risk_per_trade_pct=risk_per_trade_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_drawdown_pct=max_drawdown_pct,
    )
    engine = BacktestEngine(risk_manager)
    result = engine.run(data, strategy)

    return {
        "source": source,
        "pair": pair if source == "kraken" else None,
        "equity_curve": [
            {"date": d.strftime("%Y-%m-%d"), "equity": round(v, 2)}
            for d, v in result.equity_curve.items()
        ],
        "metrics": {k: round(v, 3) if isinstance(v, float) else v for k, v in result.metrics.items()},
        "trades": [
            {
                "entry_date": t.entry_date.strftime("%Y-%m-%d"),
                "exit_date": t.exit_date.strftime("%Y-%m-%d") if t.exit_date else None,
                "side": t.side,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2) if t.exit_price else None,
                "exit_reason": t.exit_reason,
                "pnl": round(t.pnl, 2) if t.pnl is not None else None,
            }
            for t in result.trades
        ],
        "halted_at": result.halted_at.strftime("%Y-%m-%d") if result.halted_at else None,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE


_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AVONAM — Tableau de bord</title>
<style>
  :root { --bg:#0b0f14; --panel:#121822; --text:#e6edf3; --muted:#8b98a5; --accent:#4c9eff; --pos:#3fb950; --neg:#f85149; --border:#232b36; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:20px 24px; border-bottom:1px solid var(--border); }
  header h1 { margin:0; font-size:20px; }
  header p { margin:4px 0 0; color:var(--muted); font-size:13px; }
  main { max-width:1100px; margin:0 auto; padding:24px; display:grid; gap:20px; }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:18px; }
  form { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:14px; align-items:end; }
  label { display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }
  input, select { width:100%; padding:8px; background:#0d1420; border:1px solid var(--border); border-radius:6px; color:var(--text); }
  .source-note { font-size:12px; color:var(--muted); margin:10px 0 0; }
  button { grid-column: -2 / -1; padding:10px 16px; background:var(--accent); border:none; border-radius:6px; color:#fff; font-weight:600; cursor:pointer; }
  button:hover { opacity:0.9; }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
  .stat { background:#0d1420; border:1px solid var(--border); border-radius:8px; padding:12px; }
  .stat .label { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .stat .value { font-size:20px; font-weight:700; margin-top:4px; }
  .pos { color:var(--pos); } .neg { color:var(--neg); }
  canvas { width:100%; height:280px; }
  table { width:100%; border-collapse:collapse; font-size:13px; margin-top:8px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); }
  th { color:var(--muted); font-weight:500; }
  .muted { color:var(--muted); font-size:12px; margin-top:8px; }
</style>
</head>
<body>
<header>
  <h1>AVONAM — Tableau de bord (simulation)</h1>
  <p>Backtest / paper trading en direct, sur données d'exemple ou sur données Kraken réelles. Aucun ordre réel, aucune clé API, aucune connexion bancaire.</p>
</header>
<main>
  <div class="panel">
    <form id="form">
      <div>
        <label>Source de données</label>
        <select name="source_pair">
          <option value="demo">Données d'exemple (statique)</option>
          <option value="kraken:XBTEUR">Kraken — BTC/EUR (temps réel)</option>
          <option value="kraken:ETHEUR">Kraken — ETH/EUR (temps réel)</option>
        </select>
      </div>
      <div><label>SMA rapide</label><input type="number" name="fast_period" value="20"></div>
      <div><label>SMA lente</label><input type="number" name="slow_period" value="50"></div>
      <div><label>Capital initial (€)</label><input type="number" name="initial_capital" value="10000"></div>
      <div><label>Risque / trade (%)</label><input type="number" step="0.1" name="risk_per_trade_pct" value="1.0"></div>
      <div><label>Stop-loss (%)</label><input type="number" step="0.1" name="stop_loss_pct" value="2.0"></div>
      <div><label>Take-profit (%)</label><input type="number" step="0.1" name="take_profit_pct" value="4.0"></div>
      <div><label>Drawdown max (%)</label><input type="number" step="0.1" name="max_drawdown_pct" value="20.0"></div>
      <button type="submit">Lancer le backtest</button>
    </form>
  </div>

  <div class="panel">
    <p class="source-note" id="source-note"></p>
    <div class="stats" id="stats"></div>
  </div>

  <div class="panel">
    <canvas id="chart"></canvas>
  </div>

  <div class="panel">
    <table id="trades"><thead><tr>
      <th>Entrée</th><th>Sortie</th><th>Sens</th><th>Prix entrée</th><th>Prix sortie</th><th>Raison</th><th>P&L</th>
    </tr></thead><tbody></tbody></table>
    <p class="muted">Backtest pédagogique : aucune garantie de performance future. Voir le README pour les limites (overfitting, biais du survivant, etc.).</p>
  </div>
</main>

<script>
const form = document.getElementById('form');
const statsEl = document.getElementById('stats');
const sourceNoteEl = document.getElementById('source-note');
const canvas = document.getElementById('chart');
const tbody = document.querySelector('#trades tbody');

const SOURCE_LABELS = {
  demo: "Données d'exemple synthétiques (statiques).",
  'kraken:XBTEUR': 'Kraken — BTC/EUR, bougies horaires en temps réel (paper trading, aucune clé API, aucun ordre).',
  'kraken:ETHEUR': 'Kraken — ETH/EUR, bougies horaires en temps réel (paper trading, aucune clé API, aucun ordre).',
};

function fmt(n) { return typeof n === 'number' ? n.toLocaleString('fr-BE', {maximumFractionDigits: 2}) : n; }

function drawChart(points) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const values = points.map(p => p.equity);
  const min = Math.min(...values), max = Math.max(...values);
  const pad = 30;
  const x = i => pad + (i / (points.length - 1)) * (w - pad * 2);
  const y = v => h - pad - ((v - min) / (max - min || 1)) * (h - pad * 2);

  ctx.strokeStyle = '#4c9eff'; ctx.lineWidth = 2; ctx.beginPath();
  points.forEach((p, i) => { const px = x(i), py = y(p.equity); i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py); });
  ctx.stroke();

  ctx.fillStyle = '#8b98a5'; ctx.font = '11px sans-serif';
  ctx.fillText(fmt(max), 4, y(max) + 4);
  ctx.fillText(fmt(min), 4, y(min) + 4);
  ctx.fillText(points[0].date, pad, h - 8);
  ctx.fillText(points[points.length - 1].date, w - 90, h - 8);
}

function renderStats(m) {
  const cls = v => v >= 0 ? 'pos' : 'neg';
  statsEl.innerHTML = `
    <div class="stat"><div class="label">Rendement total</div><div class="value ${cls(m.total_return_pct)}">${fmt(m.total_return_pct)}%</div></div>
    <div class="stat"><div class="label">Drawdown max</div><div class="value neg">${fmt(m.max_drawdown_pct)}%</div></div>
    <div class="stat"><div class="label">Ratio de Sharpe</div><div class="value">${fmt(m.sharpe_ratio)}</div></div>
    <div class="stat"><div class="label">Trades</div><div class="value">${m.num_trades}</div></div>
    <div class="stat"><div class="label">Win rate</div><div class="value">${fmt(m.win_rate_pct)}%</div></div>
    <div class="stat"><div class="label">Profit factor</div><div class="value">${fmt(m.profit_factor)}</div></div>
  `;
}

function renderTrades(trades) {
  tbody.innerHTML = trades.slice(-25).reverse().map(t => `
    <tr>
      <td>${t.entry_date}</td><td>${t.exit_date ?? '—'}</td>
      <td>${t.side === 1 ? 'Long' : 'Short'}</td>
      <td>${fmt(t.entry_price)}</td><td>${t.exit_price != null ? fmt(t.exit_price) : '—'}</td>
      <td>${t.exit_reason ?? '—'}</td>
      <td class="${t.pnl >= 0 ? 'pos' : 'neg'}">${t.pnl != null ? fmt(t.pnl) : '—'}</td>
    </tr>`).join('');
}

async function runBacktest() {
  const formData = new FormData(form);
  const sourcePair = formData.get('source_pair');
  const [source, pair] = sourcePair.split(':');
  formData.delete('source_pair');

  const params = new URLSearchParams(formData);
  params.set('source', source);
  if (pair) params.set('pair', pair);

  sourceNoteEl.textContent = 'Chargement...';
  const resp = await fetch('/api/backtest?' + params.toString());
  const data = await resp.json();
  if (data.error) { sourceNoteEl.textContent = ''; alert(data.error); return; }

  sourceNoteEl.textContent = SOURCE_LABELS[sourcePair] ?? '';
  drawChart(data.equity_curve);
  renderStats(data.metrics);
  renderTrades(data.trades);
}

form.addEventListener('submit', e => { e.preventDefault(); runBacktest(); });
window.addEventListener('resize', () => runBacktest());
runBacktest();
</script>
</body>
</html>"""
