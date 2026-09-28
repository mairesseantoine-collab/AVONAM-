"""Veille d'actualité crypto via flux RSS publics.

But prudent et unique : repérer une CRISE À L'ÉCHELLE DU MARCHÉ (effondrement
d'une grande plateforme, vague de piratages majeurs) qui justifie de
suspendre les nouvelles ouvertures. Ce n'est pas de l'analyse fine.

⚠️ Piège évité (leçon apprise) : l'actualité crypto contient TOUS LES JOURS
des titres avec « hack », « exploit », « lawsuit »... C'est le bruit de fond
normal du secteur, pas une crise. Un simple comptage de mots-clés lève alors
`risk_off` en permanence et bloque le robot. Pour éviter ça, on n'exige pas un
nombre absolu de titres à risque, mais qu'une FRACTION importante des titres
récents porte sur un événement grave : c'est la signature d'une crise qui
domine l'actualité, pas d'un incident isolé. Vocabulaire restreint aux termes
réellement systémiques, et double seuil (minimum ET fraction).

Toute erreur réseau (ou un flux indisponible) retombe silencieusement sur
neutre.
"""

from __future__ import annotations

import re

from common.http_transport import HttpTransport, RequestsTransport
from market.signal import MarketSignal

# Flux RSS publics, titres uniquement (on ne lit pas le corps).
DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]

# Termes réellement SYSTÉMIQUES seulement. On retire volontairement les mots du
# quotidien crypto (ban, lawsuit, sues, charged, fraud, sanction, seized...)
# qui, seuls, ne signalent pas une crise de marché.
_RISK_TERMS = {
    "hack", "hacked", "exploit", "breach", "stolen", "drained",
    "collapse", "collapses", "insolvent", "insolvency", "bankruptcy",
    "depeg", "depegs", "rugpull", "meltdown", "contagion", "liquidations",
}
_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_WORD = re.compile(r"[a-z]+")
_USER_AGENT = "avonam-news/1.0 (educational trading bot)"


class NewsProvider:
    def __init__(
        self,
        transport: HttpTransport | None = None,
        feeds: list[str] | None = None,
        risk_off_min_hits: int = 8,
        risk_off_fraction: float = 0.30,
    ) -> None:
        self.transport = transport or RequestsTransport()
        self.feeds = feeds or list(DEFAULT_FEEDS)
        # Deux conditions à réunir pour lever risk_off : au moins N titres à
        # risque ET une fraction importante des titres récents. Un incident
        # isolé (2 titres sur 60) ne suffit jamais.
        self.risk_off_min_hits = risk_off_min_hits
        self.risk_off_fraction = risk_off_fraction

    def evaluate(self) -> MarketSignal:
        titles = self._fetch_titles()
        if not titles:
            return MarketSignal.neutral()

        flagged: list[str] = []
        hits = 0
        for title in titles:
            words = set(_WORD.findall(title.lower()))
            if words & _RISK_TERMS:
                hits += 1
                if len(flagged) < 3:
                    flagged.append(title.strip()[:120])

        fraction = hits / len(titles)
        crisis = hits >= self.risk_off_min_hits and fraction >= self.risk_off_fraction
        if crisis:
            reasons = [
                f"Crise probable : {hits} titres graves sur {len(titles)} récents "
                f"({fraction*100:.0f} %), ouvertures suspendues ce cycle"
            ]
            reasons += [f"• {t}" for t in flagged]
            return MarketSignal(bias=-0.2, risk_off=True, reasons=reasons)
        if hits > 0:
            return MarketSignal(bias=0.0, risk_off=False,
                                reasons=[f"{hits}/{len(titles)} titres à risque ({fraction*100:.0f} %), sous le seuil de crise"])
        return MarketSignal.neutral()

    def _fetch_titles(self) -> list[str]:
        titles: list[str] = []
        for url in self.feeds:
            try:
                resp = self.transport.get(url, headers={"User-Agent": _USER_AGENT})
                xml = resp.text or ""
                # On saute le premier <title> (titre du flux lui-même).
                found = _TITLE.findall(xml)
                for raw in (found[1:] if len(found) > 1 else found):
                    titles.append(_clean_title(raw))
            except Exception:
                continue
        return titles


def _clean_title(raw: str) -> str:
    """Retire l'emballage CDATA des titres RSS pour des logs lisibles."""
    m = _CDATA.search(raw)
    return (m.group(1) if m else raw).strip()
