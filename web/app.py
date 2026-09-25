"""Interface web du moteur de trading : tableau de bord pédagogique,
auto-actualisé, avec une explication pour chaque donnée affichée.

⚠️ Ce serveur expose le moteur de trading (`avonam/`) et, en lecture seule,
les données de marché PUBLIQUES de Kraken (`broker/kraken/market_data.py`,
aucune clé API, aucun ordre) pour faire du paper trading sur des données
réelles. Il n'expose en revanche JAMAIS :
    - le module bancaire (`bank/`) : déclenche des consentements PSD2
      réels, ne doit jamais être accessible depuis un serveur public sans
      authentification utilisateur, HTTPS, et le statut TPP requis (voir
      le README, section « Intégration bancaire PSD2 ») ;
    - l'exécution réelle sur les routes PUBLIQUES (`/`, `/api/backtest`) :
      elles ne lisent aucune clé et ne passent aucun ordre.

L'exécution réelle vit uniquement sur les routes `/live` et
`/api/live/*`, protégées par mot de passe (`AVONAM_DASHBOARD_PASSWORD`) et
désactivées tant que ce mot de passe n'est pas défini. Même authentifié,
un ordre réel exige le mode LIVE_REAL ET le mot de confirmation, et reste
plafonné par le kill switch (voir `broker/live/`). Le plafond cumulé borne
l'exposition totale quoi qu'il arrive.

Lancer en local :
    python -m uvicorn web.app:app --reload

Lancer en conteneur : voir Dockerfile / DEPLOY.md à la racine du projet.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

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

# --- Espace trading réel (PRIVÉ, protégé par mot de passe) ----------------
# Séparé du tableau de bord public : c'est le seul endroit de l'interface web
# qui touche aux clés Kraken et peut exécuter un ordre réel. Désactivé tant
# que AVONAM_DASHBOARD_PASSWORD n'est pas défini dans l'environnement Render.
_basic = HTTPBasic(auto_error=False)

CONFIRM_WORD = "EXECUTER"


def require_live_auth(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> bool:
    password = os.environ.get("AVONAM_DASHBOARD_PASSWORD")
    if not password:
        raise HTTPException(
            status_code=503,
            detail="Espace trading réel non configuré (variable AVONAM_DASHBOARD_PASSWORD absente).",
        )
    if credentials is None or not secrets.compare_digest(credentials.password, password):
        raise HTTPException(status_code=401, detail="Accès refusé.", headers={"WWW-Authenticate": "Basic"})
    return True


def _build_live_session():
    """Construit une session de trading réel à partir de l'environnement.
    Isolé dans une fonction pour être remplaçable dans les tests."""
    from broker.killswitch import TradingKillSwitch
    from broker.kraken.client import KrakenClient
    from broker.live.agent import RuleBasedAgent
    from broker.live.config import LiveTradingConfig
    from broker.live.session import LiveTradingSession
    from broker.live.strategy import build_live_strategy
    from common.audit_log import AuditLog
    from common.http_transport import RequestsTransport

    config = LiveTradingConfig.from_env()
    client = KrakenClient(
        RequestsTransport(),
        api_key=os.environ.get("KRAKEN_API_KEY"),
        api_secret=os.environ.get("KRAKEN_API_SECRET"),
    )
    audit = AuditLog(os.environ.get("AVONAM_AUDIT_PATH", "output/live_audit.log"))
    killswitch = TradingKillSwitch(
        max_notional_per_order=config.max_notional_per_order_eur * 1.2,
        max_notional_per_day=config.max_notional_per_day_eur,
        allowed_pairs=[config.pair],
        max_consecutive_failures=config.max_consecutive_failures,
    )
    session = LiveTradingSession(
        client=client,
        strategy=build_live_strategy(),
        agent=RuleBasedAgent(),
        killswitch=killswitch,
        audit_log=audit,
        config=config,
    )
    return session, config


def _proposal_payload(session, config) -> dict:
    proposal = session.propose()
    return {
        "mode": config.mode.value,
        "pair": config.pair,
        "caps": {
            "per_order_eur": config.max_notional_per_order_eur,
            "per_day_eur": config.max_notional_per_day_eur,
            "total_eur": config.max_total_notional_eur,
        },
        "action": proposal.decision.action,
        "confidence": proposal.decision.confidence,
        "rationale": proposal.decision.rationale,
        "has_order": proposal.order is not None,
        "side": proposal.order.side if proposal.order else None,
        "volume": proposal.order.volume if proposal.order else None,
        "estimated_notional_eur": proposal.estimated_notional_eur,
        "last_price": proposal.last_price,
        "allowed": proposal.allowed,
        "block_reason": proposal.block_reason,
    }


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
        return {"error": "La SMA rapide doit être strictement plus courte que la SMA lente."}

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
        "last_price": round(float(data["close"].iloc[-1]), 2),
        "as_of": data.index[-1].isoformat(),
        "equity_curve": [
            {"date": d.strftime("%Y-%m-%d %H:%M"), "equity": round(v, 2)}
            for d, v in result.equity_curve.items()
        ],
        "metrics": {k: round(v, 3) if isinstance(v, float) else v for k, v in result.metrics.items()},
        "trades": [
            {
                "entry_date": t.entry_date.strftime("%Y-%m-%d %H:%M"),
                "exit_date": t.exit_date.strftime("%Y-%m-%d %H:%M") if t.exit_date else None,
                "side": t.side,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2) if t.exit_price else None,
                "exit_reason": t.exit_reason,
                "pnl": round(t.pnl, 2) if t.pnl is not None else None,
            }
            for t in result.trades
        ],
        "halted_at": result.halted_at.strftime("%Y-%m-%d %H:%M") if result.halted_at else None,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE


@app.get("/a-propos", response_class=HTMLResponse)
def about() -> str:
    return _ABOUT_PAGE


@app.get("/live", response_class=HTMLResponse)
def live_page(_auth: bool = Depends(require_live_auth)) -> str:
    return _LIVE_PAGE


@app.get("/api/live/propose")
def api_live_propose(_auth: bool = Depends(require_live_auth)) -> dict:
    try:
        session, config = _build_live_session()
        return _proposal_payload(session, config)
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/live/account")
def api_live_account(_auth: bool = Depends(require_live_auth)) -> dict:
    """État réel du compte Kraken, source de vérité quel que soit ce qui a
    passé les ordres (worker ou page). Lecture seule."""
    from broker.live.session import _BASE_ASSET

    try:
        session, config = _build_live_session()
        client = session.client
        balances = {b.asset: b.amount for b in client.get_balance()}
        base_asset = _BASE_ASSET.get(config.pair)
        base_amount = balances.get(base_asset, 0.0) if base_asset else 0.0
        last_price = float(client.get_ticker(config.pair)["c"][0])
        return {
            "eur": round(balances.get("ZEUR", 0.0), 2),
            "base_asset": base_asset,
            "base_amount": base_amount,
            "base_value_eur": round(base_amount * last_price, 2),
            "last_price": round(last_price, 2),
            "open_orders": client.get_open_orders(),
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/live/execute")
def api_live_execute(body: dict, _auth: bool = Depends(require_live_auth)) -> dict:
    # Le clic authentifié + le mot de confirmation constituent la
    # confirmation humaine. En mode SHADOW, confirm_and_execute refuse de
    # toute façon (aucun ordre réel), c'est une double sécurité.
    if body.get("confirm") != CONFIRM_WORD:
        return {"executed": False, "reason": f'Confirmation absente (attendu "{CONFIRM_WORD}").'}

    try:
        session, config = _build_live_session()
        proposal = session.propose()  # proposition fraîche, jamais une proposition périmée
        if proposal.order is None:
            return {"executed": False, "reason": proposal.block_reason or "Aucun ordre à exécuter (attente)."}
        result = session.confirm_and_execute(proposal, human_confirmed=True)
        if result is None:
            return {"executed": False, "reason": "Refusé par les garde-fous (mode SHADOW ou plafond), voir l'audit."}
        return {"executed": True, "status": result.status, "order_id": result.order_id}
    except Exception as exc:
        return {"executed": False, "reason": str(exc)}


_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AVONAM — Tableau de bord</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    color-scheme: dark;
    --bg:#0e1117; --panel:#161b24; --panel-2:#0d1420; --border:#252c38;
    --text:#e9edf4; --muted:#8b96a8; --accent:#2a78d6; --accent-2:#3987e5;
    --good:#17c317; --critical:#e66767; --good-bg:rgba(23,195,23,.12); --critical-bg:rgba(230,103,103,.12);
    --warn:#e9a64f; --warn-bg:rgba(233,166,79,.12);
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:"IBM Plex Sans",-apple-system,sans-serif; }
  code, .mono, output, .value, td { font-family:"IBM Plex Mono",monospace; }
  header { padding:22px 24px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }
  header .titles h1 { margin:0; font-size:21px; letter-spacing:-.01em; }
  header .titles p { margin:6px 0 0; color:var(--muted); font-size:13px; max-width:70ch; line-height:1.5; }
  header nav { display:flex; gap:8px; flex-wrap:wrap; }
  header nav a { color:var(--muted); text-decoration:none; font-size:13px; padding:7px 12px; border:1px solid var(--border); border-radius:7px; white-space:nowrap; }
  header nav a:hover { color:var(--text); border-color:var(--accent); }
  header nav a.nav-active { color:var(--text); border-color:var(--accent); background:var(--panel); }
  main { max-width:1080px; margin:0 auto; padding:22px 16px 60px; display:grid; gap:16px; }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:18px; }

  /* -- sélecteur de source (pills) -- */
  .pills { display:flex; gap:8px; flex-wrap:wrap; }
  .pill { padding:8px 14px; border-radius:999px; border:1px solid var(--border); background:var(--panel-2); color:var(--muted); font-size:13px; cursor:pointer; font-weight:500; display:inline-flex; align-items:center; gap:7px; }
  .pill.active { background:var(--accent); border-color:var(--accent); color:#fff; }
  .tag { font-size:10px; padding:1px 7px; border-radius:999px; font-weight:600; text-transform:uppercase; letter-spacing:.03em; }
  .tag-live { background:var(--good-bg); color:var(--good); }
  .tag-soon { background:var(--warn-bg); color:var(--warn); }
  .pill.active .tag-live, .pill.active .tag-soon { background:rgba(255,255,255,.2); color:#fff; }
  .soon-note { margin-top:16px; background:var(--panel-2); border:1px solid var(--border); border-radius:9px; padding:16px; font-size:13.5px; color:#c7cedb; line-height:1.6; }
  .soon-note b { color:var(--text); }
  .soon-note .steps { margin:10px 0 0; padding-left:20px; }
  .soon-note .steps li { margin-bottom:5px; }
  .live-row { display:flex; align-items:center; gap:10px; margin-top:12px; flex-wrap:wrap; font-size:12px; color:var(--muted); }
  .live-dot { width:8px; height:8px; border-radius:50%; background:var(--muted); }
  .live-dot.on { background:var(--good); box-shadow:0 0 0 0 var(--good); animation:pulse 1.8s infinite; }
  @keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(23,195,23,.5);} 70%{box-shadow:0 0 0 6px rgba(23,195,23,0);} 100%{box-shadow:0 0 0 0 rgba(23,195,23,0);} }
  .toggle { display:inline-flex; align-items:center; gap:6px; cursor:pointer; user-select:none; }
  .toggle input { accent-color:var(--accent); }
  .refresh-btn { margin-left:auto; padding:6px 12px; border-radius:6px; border:1px solid var(--border); background:var(--panel-2); color:var(--text); font-size:12px; cursor:pointer; }
  .refresh-btn:hover { border-color:var(--accent); }

  /* -- réglages avancés -- */
  details.panel summary { cursor:pointer; font-weight:600; font-size:14px; list-style:none; }
  details.panel summary::-webkit-details-marker { display:none; }
  details.panel summary::before { content:"▸ "; color:var(--muted); }
  details.panel[open] summary::before { content:"▾ "; }
  .sliders { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:18px; margin-top:16px; }
  .slider-row label { display:flex; align-items:center; font-size:12px; color:var(--muted); margin-bottom:6px; }
  .slider-line { display:flex; align-items:center; gap:10px; }
  input[type=range] { flex:1; accent-color:var(--accent); }
  .slider-line output { font-size:13px; min-width:48px; text-align:right; }
  .capital-row { margin-top:16px; max-width:220px; }
  .capital-row label { display:block; font-size:12px; color:var(--muted); margin-bottom:6px; }
  .capital-row input { width:100%; padding:8px; background:var(--panel-2); border:1px solid var(--border); border-radius:6px; color:var(--text); font-family:"IBM Plex Mono",monospace; }

  /* -- info tooltip -- */
  .info { display:inline-flex; align-items:center; justify-content:center; width:14px; height:14px; border-radius:50%; background:var(--border); color:var(--muted); font-size:10px; cursor:help; margin-left:5px; position:relative; flex-shrink:0; }
  .info::after { content:attr(data-tip); position:absolute; bottom:135%; left:50%; transform:translateX(-50%); background:#0a0e15; border:1px solid var(--border); padding:9px 11px; border-radius:8px; font-size:11.5px; line-height:1.5; color:var(--text); width:220px; white-space:normal; z-index:20; opacity:0; pointer-events:none; transition:opacity .1s; font-weight:400; }
  .info:hover::after, .info:focus::after { opacity:1; }

  /* -- erreur -- */
  .error-banner { display:none; background:var(--critical-bg); border:1px solid var(--critical); color:var(--critical); padding:10px 14px; border-radius:8px; font-size:13px; margin-bottom:14px; }
  .error-banner.show { display:block; }

  .source-note { font-size:12.5px; color:var(--muted); margin:0 0 14px; }
  .price-badge { color:var(--text); font-weight:600; }

  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }
  .stat { background:var(--panel-2); border:1px solid var(--border); border-radius:9px; padding:12px; }
  .stat .label { display:flex; align-items:center; font-size:10.5px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .stat .value { font-size:19px; font-weight:600; margin-top:5px; }
  .good { color:var(--good); } .critical { color:var(--critical); }

  .chart-box { position:relative; }
  svg { width:100%; height:300px; display:block; overflow:visible; }
  svg text { fill:var(--muted); font-size:11px; font-family:"IBM Plex Mono",monospace; }
  .gridline { stroke:var(--border); stroke-width:1; }
  .baseline { stroke:var(--muted); stroke-width:1; stroke-dasharray:3 3; opacity:.6; }
  .equity-line { fill:none; stroke:var(--accent-2); stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }
  .equity-area { fill:rgba(57,135,229,.10); stroke:none; }
  .crosshair { stroke:var(--muted); stroke-width:1; opacity:0; pointer-events:none; }
  .chart-tooltip { position:absolute; pointer-events:none; background:var(--panel-2); border:1px solid var(--border); border-radius:8px; padding:7px 10px; font-size:12px; opacity:0; transform:translate(-50%,-115%); transition:opacity .1s; white-space:nowrap; }
  .chart-tooltip .t-date { color:var(--muted); font-size:10.5px; }

  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--border); white-space:nowrap; }
  th { color:var(--muted); font-weight:500; font-size:11px; text-transform:uppercase; letter-spacing:.03em; }
  .badge { font-family:"IBM Plex Sans",sans-serif; font-size:10.5px; padding:2px 8px; border-radius:999px; }
  .badge.long { background:rgba(57,135,229,.15); color:#7ab4f2; }
  .badge.short { background:rgba(233,140,20,.15); color:#e9a64f; }
  .badge.reason-stop_loss { background:var(--critical-bg); color:var(--critical); }
  .badge.reason-take_profit { background:var(--good-bg); color:var(--good); }
  .badge.reason-signal { background:var(--border); color:var(--muted); }

  details.help summary { cursor:pointer; font-weight:600; font-size:14px; list-style:none; }
  details.help summary::-webkit-details-marker { display:none; }
  details.help summary::before { content:"▸ "; color:var(--muted); }
  details.help[open] summary::before { content:"▾ "; }
  details.help .help-body { margin-top:14px; display:grid; gap:10px; font-size:13px; color:var(--muted); line-height:1.6; }
  details.help .help-body b { color:var(--text); }
  .muted-note { font-size:12px; color:var(--muted); margin-top:10px; }
</style>
</head>
<body>
<header>
  <div class="titles">
    <h1>AVONAM — Tableau de bord (simulation)</h1>
    <p>Backtest et paper trading en direct, sur données d'exemple ou sur données Kraken réelles, avec explication de chaque chiffre affiché. Aucun ordre réel, aucune clé API, aucune connexion bancaire — voir « Comprendre ce tableau de bord » en bas de page.</p>
  </div>
  <nav>
    <a href="/" class="nav-active">Tableau de bord</a>
    <a href="/a-propos">À propos</a>
    <a href="/live">Espace privé</a>
  </nav>
</header>
<main>

  <div class="panel">
    <div style="font-size:12px;color:var(--muted);margin-bottom:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">Classe d'actifs</div>
    <div class="pills" id="asset-pills">
      <button type="button" class="pill active" data-asset="crypto">Crypto <span class="tag tag-live">disponible</span></button>
      <button type="button" class="pill" data-asset="stock">Actions <span class="tag tag-soon">à venir</span></button>
      <button type="button" class="pill" data-asset="commodity">Matières premières <span class="tag tag-soon">à venir</span></button>
    </div>

    <div id="crypto-sources">
      <div style="font-size:12px;color:var(--muted);margin:16px 0 8px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">Source de données</div>
      <div class="pills" id="source-pills">
        <button type="button" class="pill active" data-source="demo" data-pair="">Données d'exemple</button>
        <button type="button" class="pill" data-source="kraken" data-pair="XBTEUR">Kraken · BTC/EUR</button>
        <button type="button" class="pill" data-source="kraken" data-pair="ETHEUR">Kraken · ETH/EUR</button>
      </div>
      <div class="live-row" id="live-row" style="display:none;">
        <span class="live-dot" id="live-dot"></span>
        <span id="freshness">—</span>
        <label class="toggle"><input type="checkbox" id="auto-refresh-toggle" checked> Actualisation auto. (5 min)</label>
        <button type="button" class="refresh-btn" id="refresh-now">Actualiser maintenant</button>
      </div>
    </div>

    <div id="soon-note" class="soon-note" style="display:none;"></div>
  </div>

  <div id="crypto-analysis">
  <details class="panel">
    <summary>Réglages avancés (stratégie et gestion du risque)</summary>
    <div class="sliders">
      <div class="slider-row">
        <label>SMA rapide <span class="info" tabindex="0" data-tip="Moyenne mobile courte : réagit vite aux variations de prix. Plus elle est courte, plus la stratégie change d'avis souvent.">?</span></label>
        <div class="slider-line"><input type="range" id="fast_period" min="2" max="50" step="1" value="20"><output>20</output></div>
      </div>
      <div class="slider-row">
        <label>SMA lente <span class="info" tabindex="0" data-tip="Moyenne mobile longue : donne la tendance de fond. La stratégie achète quand la rapide passe au-dessus de la lente.">?</span></label>
        <div class="slider-line"><input type="range" id="slow_period" min="5" max="200" step="1" value="50"><output>50</output></div>
      </div>
      <div class="slider-row">
        <label>Risque par trade <span class="info" tabindex="0" data-tip="Pourcentage du capital qu'on accepte de perdre si le stop-loss est touché sur UN SEUL trade. 1% est une valeur prudente classique.">?</span></label>
        <div class="slider-line"><input type="range" id="risk_per_trade_pct" min="0.1" max="5" step="0.1" value="1.0"><output>1.0%</output></div>
      </div>
      <div class="slider-row">
        <label>Stop-loss <span class="info" tabindex="0" data-tip="Distance sous le prix d'entrée à laquelle la position se ferme automatiquement pour limiter la perte.">?</span></label>
        <div class="slider-line"><input type="range" id="stop_loss_pct" min="0.5" max="10" step="0.1" value="2.0"><output>2.0%</output></div>
      </div>
      <div class="slider-row">
        <label>Take-profit <span class="info" tabindex="0" data-tip="Distance au-dessus du prix d'entrée à laquelle la position se ferme automatiquement pour sécuriser le gain.">?</span></label>
        <div class="slider-line"><input type="range" id="take_profit_pct" min="0.5" max="20" step="0.1" value="4.0"><output>4.0%</output></div>
      </div>
      <div class="slider-row">
        <label>Drawdown max <span class="info" tabindex="0" data-tip="Si le capital chute de plus de ce pourcentage depuis son plus haut, la stratégie arrête d'ouvrir de nouvelles positions (kill switch).">?</span></label>
        <div class="slider-line"><input type="range" id="max_drawdown_pct" min="5" max="50" step="1" value="20"><output>20%</output></div>
      </div>
    </div>
    <div class="capital-row">
      <label>Capital initial (€)</label>
      <input type="number" id="initial_capital" value="10000" min="100" step="100">
    </div>
  </details>

  <div class="panel">
    <div class="error-banner" id="error-banner"></div>
    <p class="source-note" id="source-note">Chargement…</p>
    <div class="stats" id="stats"></div>
  </div>

  <div class="panel">
    <div class="chart-box">
      <svg id="chart" viewBox="0 0 1000 300" preserveAspectRatio="none"></svg>
      <div class="chart-tooltip" id="chart-tooltip"><div class="t-date" id="tt-date"></div><div id="tt-value"></div></div>
    </div>
  </div>

  <div class="panel">
    <table id="trades"><thead><tr>
      <th>Entrée</th><th>Sortie</th><th>Sens</th><th>Prix entrée</th><th>Prix sortie</th><th>Raison</th><th>P&amp;L</th>
    </tr></thead><tbody></tbody></table>
  </div>
  </div>

  <details class="panel help">
    <summary>Comprendre ce tableau de bord</summary>
    <div class="help-body">
      <div><b>Backtest vs paper trading.</b> Sur « Données d'exemple », le moteur rejoue un historique fixe d'un coup (backtest). Sur « Kraken », il utilise les dernières bougies réelles du marché — c'est du paper trading sur données réelles : aucun argent n'est engagé, mais les conditions de marché sont actuelles.</div>
      <div><b>Rendement total</b> — variation du capital sur toute la période, en %. Positif = gain simulé, négatif = perte simulée.</div>
      <div><b>Drawdown maximum</b> — la pire chute du capital par rapport à son plus haut atteint. Souvent le chiffre le plus parlant pour juger si une stratégie serait supportable psychologiquement.</div>
      <div><b>Ratio de Sharpe</b> — rendement ajusté du risque. Au-dessus de 1 est correct, au-dessus de 2 est très bon — mais peu fiable si le nombre de trades est faible.</div>
      <div><b>Win rate</b> — pourcentage de trades gagnants. Un chiffre bas peut rester rentable si les gains sont en moyenne plus gros que les pertes (voir profit factor).</div>
      <div><b>Profit factor</b> — somme des gains ÷ somme des pertes. Supérieur à 1 = stratégie gagnante sur cette période, inférieur à 1 = perdante.</div>
      <div><b>Raison de sortie</b> — <span class="badge reason-signal">signal</span> la stratégie a changé d'avis, <span class="badge reason-stop_loss">stop_loss</span> perte limitée automatiquement, <span class="badge reason-take_profit">take_profit</span> gain sécurisé automatiquement.</div>
      <div><b>Limites à garder en tête</b> — sur-optimisation (ne réglez pas les paramètres juste pour maximiser ce backtest précis), biais du survivant, et l'exécution réelle peut toujours être légèrement pire (slippage, frais). Voir le README du projet pour le détail.</div>
    </div>
  </details>

</main>

<script>
const state = { source: 'demo', pair: '' };
let debounceTimer = null;
let autoRefreshTimer = null;
let freshnessTimer = null;
let lastFetchAt = null;

const pillsEl = document.getElementById('source-pills');
const liveRowEl = document.getElementById('live-row');
const liveDotEl = document.getElementById('live-dot');
const freshnessEl = document.getElementById('freshness');
const autoToggleEl = document.getElementById('auto-refresh-toggle');
const refreshNowBtn = document.getElementById('refresh-now');
const errorBannerEl = document.getElementById('error-banner');
const sourceNoteEl = document.getElementById('source-note');
const statsEl = document.getElementById('stats');
const svg = document.getElementById('chart');
const tooltipEl = document.getElementById('chart-tooltip');
const ttDate = document.getElementById('tt-date');
const ttValue = document.getElementById('tt-value');
const tbody = document.querySelector('#trades tbody');
const capitalInput = document.getElementById('initial_capital');

const SLIDER_IDS = ['fast_period','slow_period','risk_per_trade_pct','stop_loss_pct','take_profit_pct','max_drawdown_pct'];
const SLIDER_SUFFIX = { fast_period:'', slow_period:'', risk_per_trade_pct:'%', stop_loss_pct:'%', take_profit_pct:'%', max_drawdown_pct:'%' };

// -- classes d'actifs : crypto disponible, actions/matières premières à venir --
const assetPillsEl = document.getElementById('asset-pills');
const cryptoSourcesEl = document.getElementById('crypto-sources');
const cryptoAnalysisEl = document.getElementById('crypto-analysis');
const soonNoteEl = document.getElementById('soon-note');

const SOON_CONTENT = {
  stock: {
    name: 'Actions',
    examples: 'actions et ETF (Apple, Tesla, indices...)',
    broker: 'Alpaca ou Interactive Brokers',
  },
  commodity: {
    name: 'Matières premières',
    examples: 'or, pétrole, gaz... (via contrats à terme)',
    broker: 'Interactive Brokers',
  },
};

assetPillsEl.addEventListener('click', e => {
  const btn = e.target.closest('.pill');
  if (!btn) return;
  [...assetPillsEl.children].forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  const asset = btn.dataset.asset;

  if (asset === 'crypto') {
    cryptoSourcesEl.style.display = '';
    cryptoAnalysisEl.style.display = '';
    soonNoteEl.style.display = 'none';
    runBacktest();
  } else {
    const c = SOON_CONTENT[asset];
    cryptoSourcesEl.style.display = 'none';
    cryptoAnalysisEl.style.display = 'none';
    soonNoteEl.style.display = 'block';
    soonNoteEl.innerHTML = `
      <b>${c.name} — à venir.</b> Le trading de ${c.examples} n'est pas encore actif sur ce site.
      L'architecture est déjà prête à l'accueillir (couche « place de marché » commune à tous les actifs),
      mais deux choses restent nécessaires :
      <ol class="steps">
        <li>Ouvrir un compte chez un courtier qui expose une API pour ces actifs (${c.broker}), avec ses clés.</li>
        <li>Y brancher un petit adaptateur : tout le reste (stratégie, gestion du risque, plafonds, journal, confirmation) fonctionnera à l'identique.</li>
      </ol>
      Kraken, utilisé pour le crypto, ne couvre pas ces marchés, d'où le besoin d'un autre courtier. Et comme pour le crypto, aucun de ces marchés n'offre de gain garanti.`;
  }
});

function fmt(n) { return typeof n === 'number' ? n.toLocaleString('fr-BE', {maximumFractionDigits: 2}) : n; }

// -- réglages : sliders avec lecture live + auto-run debounced --
SLIDER_IDS.forEach(id => {
  const input = document.getElementById(id);
  const output = input.nextElementSibling;
  input.addEventListener('input', () => {
    output.textContent = (id.includes('period') ? input.value : parseFloat(input.value).toFixed(1)) + SLIDER_SUFFIX[id];
    scheduleRun();
  });
});
capitalInput.addEventListener('input', scheduleRun);

function scheduleRun() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runBacktest, 400);
}

// -- sélecteur de source (pills) --
pillsEl.addEventListener('click', e => {
  const btn = e.target.closest('.pill');
  if (!btn) return;
  [...pillsEl.children].forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  state.source = btn.dataset.source;
  state.pair = btn.dataset.pair;
  liveRowEl.style.display = state.source === 'kraken' ? 'flex' : 'none';
  liveDotEl.classList.toggle('on', state.source === 'kraken');
  setupAutoRefresh();
  runBacktest();
});

autoToggleEl.addEventListener('change', setupAutoRefresh);
refreshNowBtn.addEventListener('click', runBacktest);

function setupAutoRefresh() {
  clearInterval(autoRefreshTimer);
  if (state.source === 'kraken' && autoToggleEl.checked) {
    autoRefreshTimer = setInterval(runBacktest, 5 * 60 * 1000);
  }
}

function updateFreshness() {
  if (!lastFetchAt) { freshnessEl.textContent = '—'; return; }
  const seconds = Math.round((Date.now() - lastFetchAt) / 1000);
  const label = seconds < 60 ? `il y a ${seconds}s` : `il y a ${Math.round(seconds / 60)} min`;
  freshnessEl.textContent = `Dernière mise à jour : ${label}`;
}
freshnessTimer = setInterval(updateFreshness, 1000);

// -- graphique (SVG avec crosshair + info-bulle) --
function renderChart(points) {
  svg.innerHTML = '';
  const W = 1000, H = 300, padL = 60, padR = 16, padT = 16, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const values = points.map(p => p.equity);
  const minV = Math.min(...values), maxV = Math.max(...values);
  const span = (maxV - minV) || 1;
  const initial = points[0].equity;

  const xAt = i => padL + (i / (points.length - 1)) * plotW;
  const yAt = v => padT + plotH - ((v - minV) / span) * plotH;
  const ns = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

  for (let s = 0; s <= 4; s++) {
    const v = minV + (span * s / 4);
    const y = yAt(v);
    svg.appendChild(el('line', { x1:padL, x2:W-padR, y1:y, y2:y, class:'gridline' }));
    const t = el('text', { x:6, y:y+4 }); t.textContent = Math.round(v).toLocaleString('fr-BE'); svg.appendChild(t);
  }
  const yBase = yAt(initial);
  svg.appendChild(el('line', { x1:padL, x2:W-padR, y1:yBase, y2:yBase, class:'baseline' }));

  let dLine = `M ${xAt(0)} ${yAt(points[0].equity)} `, dArea = dLine;
  points.forEach((p, i) => { if (i===0) return; dLine += `L ${xAt(i)} ${yAt(p.equity)} `; dArea += `L ${xAt(i)} ${yAt(p.equity)} `; });
  dArea += `L ${xAt(points.length-1)} ${yBase} L ${xAt(0)} ${yBase} Z`;
  svg.appendChild(el('path', { d:dArea, class:'equity-area' }));
  svg.appendChild(el('path', { d:dLine, class:'equity-line' }));

  const lastI = points.length - 1, lastX = xAt(lastI), lastY = yAt(points[lastI].equity);
  svg.appendChild(el('circle', { cx:lastX, cy:lastY, r:4, fill:'var(--accent-2)', stroke:'var(--panel)', 'stroke-width':2 }));

  svg.appendChild(el('text', { x:padL, y:H-6 })).textContent = points[0].date;
  svg.appendChild(el('text', { x:W-padR, y:H-6, 'text-anchor':'end' })).textContent = points[lastI].date;

  const crosshair = el('line', { x1:0, x2:0, y1:padT, y2:H-padB, class:'crosshair' });
  svg.appendChild(crosshair);
  const hit = el('rect', { x:padL, y:padT, width:plotW, height:plotH, fill:'transparent' });
  svg.appendChild(hit);

  function show(clientX) {
    const rect = svg.getBoundingClientRect();
    const xSvg = ((clientX - rect.left) / rect.width) * W;
    const ratio = Math.min(Math.max((xSvg - padL) / plotW, 0), 1);
    const i = Math.round(ratio * (points.length - 1));
    const p = points[i];
    crosshair.setAttribute('x1', xAt(i)); crosshair.setAttribute('x2', xAt(i)); crosshair.style.opacity = 1;
    ttDate.textContent = p.date;
    ttValue.textContent = fmt(p.equity) + ' €';
    ttValue.style.color = p.equity >= initial ? 'var(--good)' : 'var(--critical)';
    const boxRect = svg.parentElement.getBoundingClientRect();
    tooltipEl.style.left = (clientX - boxRect.left) + 'px';
    tooltipEl.style.top = ((yAt(p.equity) / H) * boxRect.height) + 'px';
    tooltipEl.style.opacity = 1;
  }
  hit.addEventListener('pointermove', e => show(e.clientX));
  hit.addEventListener('pointerleave', () => { crosshair.style.opacity = 0; tooltipEl.style.opacity = 0; });
}

function renderStats(m) {
  const cls = v => v >= 0 ? 'good' : 'critical';
  statsEl.innerHTML = `
    <div class="stat"><div class="label">Rendement total <span class="info" tabindex="0" data-tip="Variation du capital sur toute la période, en %. Positif = gain, négatif = perte.">?</span></div><div class="value ${cls(m.total_return_pct)}">${fmt(m.total_return_pct)}%</div></div>
    <div class="stat"><div class="label">Drawdown max <span class="info" tabindex="0" data-tip="Pire chute du capital depuis son plus haut. Un chiffre élevé est difficile à supporter psychologiquement.">?</span></div><div class="value critical">${fmt(m.max_drawdown_pct)}%</div></div>
    <div class="stat"><div class="label">Ratio de Sharpe <span class="info" tabindex="0" data-tip="Rendement ajusté du risque. >1 correct, >2 très bon — peu fiable sur peu de trades.">?</span></div><div class="value">${fmt(m.sharpe_ratio)}</div></div>
    <div class="stat"><div class="label">Trades <span class="info" tabindex="0" data-tip="Nombre total d'allers-retours (entrée puis sortie) sur la période.">?</span></div><div class="value">${m.num_trades}</div></div>
    <div class="stat"><div class="label">Win rate <span class="info" tabindex="0" data-tip="Pourcentage de trades gagnants. Peut être bas et quand même rentable, voir profit factor.">?</span></div><div class="value">${fmt(m.win_rate_pct)}%</div></div>
    <div class="stat"><div class="label">Profit factor <span class="info" tabindex="0" data-tip="Somme des gains ÷ somme des pertes. >1 = rentable sur cette période.">?</span></div><div class="value">${fmt(m.profit_factor)}</div></div>
  `;
}

function renderTrades(trades) {
  tbody.innerHTML = trades.slice(-25).reverse().map(t => `
    <tr>
      <td>${t.entry_date}</td><td>${t.exit_date ?? '—'}</td>
      <td><span class="badge ${t.side === 1 ? 'long' : 'short'}">${t.side === 1 ? 'Long' : 'Short'}</span></td>
      <td>${fmt(t.entry_price)}</td><td>${t.exit_price != null ? fmt(t.exit_price) : '—'}</td>
      <td>${t.exit_reason ? `<span class="badge reason-${t.exit_reason}">${t.exit_reason}</span>` : '—'}</td>
      <td class="${t.pnl >= 0 ? 'good' : 'critical'}">${t.pnl != null ? fmt(t.pnl) : '—'}</td>
    </tr>`).join('') || '<tr><td colspan="7" style="color:var(--muted)">Aucun trade sur cette période.</td></tr>';
}

async function runBacktest() {
  const params = new URLSearchParams({ source: state.source, pair: state.pair, initial_capital: capitalInput.value });
  SLIDER_IDS.forEach(id => params.set(id, document.getElementById(id).value));

  errorBannerEl.classList.remove('show');
  const resp = await fetch('/api/backtest?' + params.toString());
  const data = await resp.json();

  if (data.error) {
    errorBannerEl.textContent = data.error;
    errorBannerEl.classList.add('show');
    return;
  }

  lastFetchAt = Date.now();
  updateFreshness();

  if (data.source === 'kraken') {
    sourceNoteEl.innerHTML = `Kraken — ${data.pair} · dernier prix <span class="price-badge">${fmt(data.last_price)} €</span> · bougies horaires, paper trading, aucune clé API, aucun ordre.`;
  } else {
    sourceNoteEl.textContent = "Données d'exemple synthétiques (statiques, générées pour la démo).";
  }

  renderChart(data.equity_curve);
  renderStats(data.metrics);
  renderTrades(data.trades);
}

setupAutoRefresh();
runBacktest();
</script>
</body>
</html>"""


_LIVE_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AVONAM — Trading réel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root { color-scheme:dark; --bg:#0e1117; --panel:#161b24; --panel-2:#0d1420; --border:#252c38; --text:#e9edf4; --muted:#8b96a8; --accent:#2a78d6; --good:#17c317; --critical:#e66767; --warn:#e9a64f; --good-bg:rgba(23,195,23,.12); --critical-bg:rgba(230,103,103,.12); --warn-bg:rgba(233,166,79,.12); }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:"IBM Plex Sans",-apple-system,sans-serif; }
  header { padding:22px 24px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; }
  header h1 { margin:0; font-size:20px; }
  header nav { display:flex; gap:8px; flex-wrap:wrap; }
  header nav a { color:var(--muted); text-decoration:none; font-size:13px; padding:7px 12px; border:1px solid var(--border); border-radius:7px; }
  header nav a:hover { color:var(--text); border-color:var(--accent); }
  header nav a.nav-active { color:var(--text); border-color:var(--accent); background:var(--panel); }
  main { max-width:720px; margin:0 auto; padding:24px 16px 60px; display:grid; gap:16px; }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:18px 20px; }
  .mode-banner { padding:10px 14px; border-radius:9px; font-size:13.5px; font-weight:600; }
  .mode-shadow { background:var(--warn-bg); color:var(--warn); border:1px solid rgba(233,166,79,.3); }
  .mode-live { background:var(--critical-bg); color:var(--critical); border:1px solid rgba(230,103,103,.35); }
  .row { display:flex; justify-content:space-between; gap:12px; padding:7px 0; border-bottom:1px solid var(--border); font-size:14px; }
  .row:last-child { border-bottom:none; }
  .row .k { color:var(--muted); }
  .row .v { font-family:"IBM Plex Mono",monospace; text-align:right; }
  .v.buy { color:var(--good); } .v.sell { color:var(--warn); } .v.hold { color:var(--muted); }
  .rationale { background:var(--panel-2); border:1px solid var(--border); border-radius:8px; padding:12px 14px; font-size:13.5px; color:#c7cedb; line-height:1.6; }
  .caps { font-size:12px; color:var(--muted); margin-top:10px; }
  button { padding:10px 16px; border:none; border-radius:7px; font-weight:600; cursor:pointer; font-size:14px; }
  .btn-refresh { background:var(--panel-2); color:var(--text); border:1px solid var(--border); }
  .btn-refresh:hover { border-color:var(--accent); }
  .btn-exec { background:var(--critical); color:#fff; width:100%; margin-top:12px; }
  .btn-exec:disabled { background:var(--border); color:var(--muted); cursor:not-allowed; }
  .confirm-box { margin-top:12px; }
  .confirm-box input { width:100%; padding:9px; background:var(--panel-2); border:1px solid var(--border); border-radius:6px; color:var(--text); font-family:"IBM Plex Mono",monospace; }
  .confirm-box label { display:block; font-size:12px; color:var(--muted); margin-bottom:6px; }
  .result { margin-top:12px; padding:10px 14px; border-radius:8px; font-size:13.5px; display:none; }
  .result.ok { background:var(--good-bg); color:var(--good); display:block; }
  .result.ko { background:var(--critical-bg); color:var(--critical); display:block; }
  .muted { color:var(--muted); font-size:12.5px; line-height:1.6; }
</style>
</head>
<body>
<header>
  <h1>AVONAM — Trading réel (privé)</h1>
  <nav>
    <a href="/">Tableau de bord</a>
    <a href="/a-propos">À propos</a>
    <a href="/live" class="nav-active">Espace privé</a>
  </nav>
</header>
<main>
  <div id="mode-banner" class="mode-banner mode-shadow">Chargement…</div>

  <div class="panel">
    <div style="font-size:13px;color:var(--muted);margin-bottom:8px;font-weight:600;">Ton compte Kraken (en direct)</div>
    <div id="account">Chargement…</div>
  </div>

  <div class="panel">
    <div id="proposal">Chargement de la proposition…</div>
    <div class="caps" id="caps"></div>
  </div>

  <div class="panel">
    <div class="rationale" id="rationale">—</div>
    <div id="exec-zone"></div>
    <div class="result" id="result"></div>
  </div>

  <button class="btn-refresh" id="refresh">Rafraîchir la proposition</button>

  <p class="muted">Cet espace est privé (protégé par mot de passe) et distinct du tableau de bord public. Un ordre réel n'est possible qu'en mode LIVE_REAL, après avoir tapé le mot de confirmation. En mode SHADOW, rien n'est jamais exécuté. Plafond de sécurité de fond : le total cumulé ne peut pas dépasser la limite configurée.</p>
</main>

<script>
const CONFIRM_WORD = "EXECUTER";
const bannerEl = document.getElementById('mode-banner');
const accountEl = document.getElementById('account');
const proposalEl = document.getElementById('proposal');
const capsEl = document.getElementById('caps');
const rationaleEl = document.getElementById('rationale');
const execZoneEl = document.getElementById('exec-zone');
const resultEl = document.getElementById('result');
const refreshBtn = document.getElementById('refresh');
let currentMode = 'shadow';

function fmt(n) { return typeof n === 'number' ? n.toLocaleString('fr-BE', {maximumFractionDigits: 2}) : n; }

async function loadAccount() {
  const resp = await fetch('/api/live/account');
  const d = await resp.json();
  if (d.error) {
    accountEl.innerHTML = '<span style="color:var(--muted)">Solde indisponible (clés Kraken non configurées, ou erreur : ' + d.error + ')</span>';
    return;
  }
  const orders = (d.open_orders && d.open_orders.length)
    ? d.open_orders.map(o => `<div class="row"><span class="k">Ordre en attente</span><span class="v">${o.description}</span></div>`).join('')
    : '<div class="row"><span class="k">Ordres en attente</span><span class="v">aucun</span></div>';
  accountEl.innerHTML = `
    <div class="row"><span class="k">Euros disponibles</span><span class="v">${fmt(d.eur)} €</span></div>
    <div class="row"><span class="k">Crypto détenue</span><span class="v">${d.base_amount} (${fmt(d.base_value_eur)} €)</span></div>
    ${orders}
  `;
}

async function loadProposal() {
  resultEl.className = 'result';
  proposalEl.textContent = 'Chargement…';
  execZoneEl.innerHTML = '';
  const resp = await fetch('/api/live/propose');
  const d = await resp.json();
  if (d.error) { proposalEl.innerHTML = '<span style="color:var(--critical)">Erreur : ' + d.error + '</span>'; return; }

  currentMode = d.mode;
  if (d.mode === 'live_real') {
    bannerEl.className = 'mode-banner mode-live';
    bannerEl.textContent = '● MODE RÉEL (LIVE_REAL) — un ordre confirmé engagera de l\\'argent réel';
  } else {
    bannerEl.className = 'mode-banner mode-shadow';
    bannerEl.textContent = '○ MODE SHADOW — simulation, aucun ordre réel ne sera exécuté';
  }

  const actionClass = d.action === 'buy' ? 'buy' : (d.action === 'sell' ? 'sell' : 'hold');
  proposalEl.innerHTML = `
    <div class="row"><span class="k">Paire</span><span class="v">${d.pair}</span></div>
    <div class="row"><span class="k">Dernier prix</span><span class="v">${fmt(d.last_price)} €</span></div>
    <div class="row"><span class="k">Décision de l'agent</span><span class="v ${actionClass}">${d.action.toUpperCase()} (${Math.round(d.confidence*100)}%)</span></div>
    ${d.has_order ? `<div class="row"><span class="k">Ordre proposé</span><span class="v ${actionClass}">${d.side.toUpperCase()} ${d.volume} (~${fmt(d.estimated_notional_eur)} €)</span></div>` : ''}
    ${!d.allowed && d.block_reason ? `<div class="row"><span class="k">Bloqué</span><span class="v" style="color:var(--warn)">${d.block_reason}</span></div>` : ''}
  `;
  capsEl.textContent = `Plafonds : ${d.caps.per_order_eur} €/ordre · ${d.caps.per_day_eur} €/jour · ${d.caps.total_eur} € cumulés`;
  rationaleEl.textContent = d.rationale;

  if (d.has_order && d.allowed) {
    if (d.mode === 'live_real') {
      execZoneEl.innerHTML = `
        <div class="confirm-box">
          <label>Pour exécuter cet ordre RÉEL, tapez ${CONFIRM_WORD} ci-dessous :</label>
          <input id="confirm-input" autocomplete="off" placeholder="${CONFIRM_WORD}">
        </div>
        <button class="btn-exec" id="exec-btn">Confirmer et exécuter l'ordre réel</button>`;
      document.getElementById('exec-btn').addEventListener('click', execute);
    } else {
      execZoneEl.innerHTML = `<button class="btn-exec" disabled>Exécution désactivée (mode SHADOW)</button>`;
    }
  } else {
    execZoneEl.innerHTML = `<p class="muted">Aucun ordre à confirmer pour l'instant.</p>`;
  }
}

async function execute() {
  const confirm = document.getElementById('confirm-input').value.trim();
  const btn = document.getElementById('exec-btn');
  btn.disabled = true; btn.textContent = 'Envoi…';
  const resp = await fetch('/api/live/execute', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirm}),
  });
  const d = await resp.json();
  if (d.executed) {
    resultEl.className = 'result ok';
    resultEl.textContent = `Ordre envoyé : statut ${d.status}, id ${d.order_id}`;
  } else {
    resultEl.className = 'result ko';
    resultEl.textContent = 'Non exécuté : ' + (d.reason || 'raison inconnue');
  }
  setTimeout(refreshAll, 1500);
}

function refreshAll() { loadAccount(); loadProposal(); }
refreshBtn.addEventListener('click', refreshAll);
refreshAll();
</script>
</body>
</html>"""


_ABOUT_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AVONAM — À propos</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    color-scheme: dark;
    --bg:#0e1117; --panel:#161b24; --panel-2:#0d1420; --border:#252c38;
    --text:#e9edf4; --muted:#8b96a8; --accent:#2a78d6;
    --good:#17c317; --critical:#e66767; --good-bg:rgba(23,195,23,.12); --critical-bg:rgba(230,103,103,.12);
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:"IBM Plex Sans",-apple-system,sans-serif; }
  header { padding:22px 24px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }
  header h1 { margin:0; font-size:21px; letter-spacing:-.01em; }
  header nav { display:flex; gap:8px; flex-wrap:wrap; }
  header nav a { color:var(--muted); text-decoration:none; font-size:13px; padding:7px 12px; border:1px solid var(--border); border-radius:7px; white-space:nowrap; }
  header nav a:hover { color:var(--text); border-color:var(--accent); }
  header nav a.nav-active { color:var(--text); border-color:var(--accent); background:var(--panel); }
  main { max-width:760px; margin:0 auto; padding:32px 16px 60px; }
  h2 { font-size:16px; margin:34px 0 12px; }
  h2:first-of-type { margin-top:0; }
  p, li { font-size:14.5px; line-height:1.7; color:#c7cedb; }
  ul { padding-left:20px; margin:10px 0; }
  li { margin-bottom:6px; }
  .lede { font-size:16px; color:var(--text); line-height:1.6; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:18px 20px; margin:14px 0; }
  .card.good { border-color:rgba(23,195,23,.3); }
  .card.critical { border-color:rgba(230,103,103,.3); }
  .card h3 { margin:0 0 8px; font-size:14px; }
  .card.good h3 { color:var(--good); }
  .card.critical h3 { color:var(--critical); }
  .badge-row { display:flex; gap:8px; flex-wrap:wrap; margin:14px 0; }
  .badge { font-size:12px; padding:5px 11px; border-radius:999px; background:var(--panel-2); border:1px solid var(--border); color:var(--muted); }
  a.back { display:inline-block; margin-top:30px; color:var(--accent); text-decoration:none; font-size:14px; }
  a.back:hover { text-decoration:underline; }
  code { font-family:"IBM Plex Mono",monospace; background:var(--panel-2); padding:1px 5px; border-radius:4px; font-size:13px; }
</style>
</head>
<body>
<header>
  <h1>AVONAM — À propos</h1>
  <nav>
    <a href="/">Tableau de bord</a>
    <a href="/a-propos" class="nav-active">À propos</a>
    <a href="/live">Espace privé</a>
  </nav>
</header>
<main>
  <p class="lede">AVONAM est un logiciel pédagogique de trading algorithmique. Il sert à apprendre et tester des stratégies de trading (achat/vente automatique selon des règles), sans jamais risquer d'argent réel sur ce site.</p>

  <h2>Ce que fait le site, concrètement</h2>
  <p>Une stratégie simple (croisement de deux moyennes mobiles) analyse des prix, décide quand « acheter » et « vendre », et le site simule ce que ça aurait donné : gains, pertes, nombre de trades, etc. Deux modes :</p>
  <ul>
    <li><b>Données d'exemple</b> : un historique fixe généré pour la démonstration, toujours le même.</li>
    <li><b>Kraken (BTC/EUR, ETH/EUR)</b> : les vraies bougies de prix du marché crypto, en lecture seule, pour voir comment la stratégie se comporterait sur des conditions de marché actuelles.</li>
  </ul>

  <div class="badge-row">
    <span class="badge">Aucun argent réel</span>
    <span class="badge">Aucun ordre envoyé</span>
    <span class="badge">Aucune clé API sur ce site</span>
    <span class="badge">Aucune connexion bancaire</span>
  </div>

  <h2>Classes d'actifs</h2>
  <p>Le système est conçu comme une plateforme multi-marchés. Aujourd'hui, le crypto est disponible ; les actions et les matières premières sont préparées mais pas encore actives.</p>
  <div class="card good">
    <h3>✓ Crypto — disponible</h3>
    <p style="margin-bottom:0">Bitcoin, Ethereum, via Kraken. Données de marché en direct, simulation, et trading réel possible (avec un compte Kraken financé et ses clés sur le worker).</p>
  </div>
  <div class="card">
    <h3>⏳ Actions et matières premières — à venir</h3>
    <p style="margin-bottom:0">Actions et ETF (via Alpaca ou Interactive Brokers), matières premières (via Interactive Brokers). L'architecture est déjà prête à les accueillir : une couche « place de marché » commune fait que le jour où un compte courtier est ouvert, il suffit d'un petit adaptateur, sans réécrire la stratégie, la gestion du risque ni les sécurités. Kraken ne couvre pas ces marchés, d'où le besoin d'un autre courtier.</p>
  </div>

  <h2>Ce qui est automatique aujourd'hui</h2>
  <div class="card good">
    <h3>✓ Automatisé, sans risque</h3>
    <ul>
      <li>La récupération des prix Kraken en temps réel (toutes les 5 minutes), sans intervention.</li>
      <li>Le calcul de la stratégie et la simulation des trades (paper trading) sur ces prix.</li>
      <li>Le calcul des statistiques (rendement, drawdown, ratio de Sharpe, etc.) et leur affichage.</li>
      <li>La gestion du risque interne à la simulation : taille de position, stop-loss, take-profit, arrêt automatique si la perte simulée dépasse un seuil (kill switch).</li>
    </ul>
    <p style="margin-bottom:0">Tout ça tourne en boucle sans qu'aucun humain n'ait à cliquer sur quoi que ce soit — mais rien de tout ça ne touche à de l'argent réel : c'est une simulation qui se répète automatiquement, pas un robot de trading.</p>
  </div>

  <h2>Ce qui n'est volontairement PAS automatique</h2>
  <div class="card critical">
    <h3>✗ Jamais automatisé ici, par choix</h3>
    <ul>
      <li><b>Aucun ordre réel</b> n'est jamais envoyé à Kraken ou à une banque depuis ce site. Le code capable de le faire existe dans le projet (modules <code>bank/</code> et <code>broker/</code>), mais n'est pas branché sur cette interface publique.</li>
      <li><b>Aucune clé API</b> n'est configurée sur ce serveur : même en cas de faille, il n'y a rien à voler ici.</li>
      <li><b>Aucune authentification</b> n'existe sur ce site : n'importe qui avec le lien peut le consulter. Ce n'est pas grave pour de la simulation en lecture seule, ce serait inacceptable pour du trading réel.</li>
    </ul>
    <p style="margin-bottom:0">Ce n'est pas une limite technique qu'il suffirait de lever : c'est une décision de sécurité. Automatiser du trading avec de l'argent réel, sans supervision, sur un serveur public non protégé, expose à des pertes qu'aucun garde-fou logiciel ne rattrape complètement.</p>
  </div>

  <h2>Et si on veut aller plus loin, vers du réel ?</h2>
  <p>C'est possible, mais ça demande des étapes conscientes, jamais un interrupteur qu'on bascule d'un coup :</p>
  <ul>
    <li>Ouvrir un compte chez un courtier ou un exchange (ex. Kraken), avec une clé API dont la permission de retrait reste désactivée.</li>
    <li>Valider le code d'exécution en local, sur votre propre machine, jamais sur un serveur public — voir <code>examples/run_kraken_live_check.py</code>.</li>
    <li>Semaines de validation en paper trading sur données réelles avant le moindre ordre réel.</li>
    <li>Un premier ordre réel avec un montant minime, sous supervision humaine directe — jamais lancé tout seul par un script.</li>
  </ul>
  <p>Chaque étape reste une décision volontaire, prise consciemment, jamais quelque chose que le logiciel déclenche de lui-même.</p>

  <a class="back" href="/">← Retour au tableau de bord</a>
</main>
</body>
</html>"""
