"""Veille d'actualité crypto via flux RSS publics.

But prudent et unique : repérer un ÉVÉNEMENT GRAVE (piratage majeur,
interdiction réglementaire, effondrement d'une plateforme) qui justifie de
suspendre les nouvelles ouvertures le temps d'un cycle. Ce n'est pas de
l'analyse fine : on compte, dans les titres récents, les occurrences d'un
petit vocabulaire de risque. Au-delà d'un seuil, on lève `risk_off`.

Volontairement grossier et transparent : mieux vaut s'abstenir une heure de
trop que d'ouvrir une position en pleine tempête. Toute erreur réseau (ou un
flux indisponible) retombe silencieusement sur neutre.
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

_RISK_TERMS = {
    "hack", "hacked", "exploit", "breach", "stolen", "theft", "drained",
    "ban", "banned", "lawsuit", "sues", "sued", "charged", "fraud",
    "collapse", "insolvent", "bankruptcy", "halt", "halted", "depeg",
    "rugpull", "rug", "crackdown", "sanction", "seized",
}
_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WORD = re.compile(r"[a-z]+")
_USER_AGENT = "avonam-news/1.0 (educational trading bot)"


class NewsProvider:
    def __init__(
        self,
        transport: HttpTransport | None = None,
        feeds: list[str] | None = None,
        risk_off_hits: int = 3,
    ) -> None:
        self.transport = transport or RequestsTransport()
        self.feeds = feeds or list(DEFAULT_FEEDS)
        self.risk_off_hits = risk_off_hits

    def evaluate(self) -> MarketSignal:
        titles = self._fetch_titles()
        if not titles:
            return MarketSignal.neutral()

        hits = 0
        flagged: list[str] = []
        for title in titles:
            words = set(_WORD.findall(title.lower()))
            if words & _RISK_TERMS:
                hits += 1
                if len(flagged) < 3:
                    flagged.append(title.strip()[:120])

        if hits >= self.risk_off_hits:
            reasons = [f"{hits} titres d'actualité à risque détectés, ouvertures suspendues ce cycle"]
            reasons += [f"• {t}" for t in flagged]
            return MarketSignal(bias=-0.2, risk_off=True, reasons=reasons)
        if hits > 0:
            return MarketSignal(bias=0.0, risk_off=False, reasons=[f"{hits} titre(s) à risque (sous le seuil)"])
        return MarketSignal.neutral()

    def _fetch_titles(self) -> list[str]:
        titles: list[str] = []
        for url in self.feeds:
            try:
                resp = self.transport.get(url, headers={"User-Agent": _USER_AGENT})
                xml = resp.text or ""
                # On saute le premier <title> (titre du flux lui-même).
                found = _TITLE.findall(xml)
                titles.extend(found[1:] if len(found) > 1 else found)
            except Exception:
                continue
        return titles
