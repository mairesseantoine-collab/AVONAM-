from common.http_transport import HttpResponse
from market.coingecko import CoinGeckoSentimentProvider
from market.composite import CompositeMarketProvider, CompositeSentimentProvider
from market.fear_greed import FearGreedProvider
from market.news import NewsProvider
from market.signal import MarketSignal, NullMarketProvider
from sentiment.testing import FakeSentimentProvider


class _JsonTransport:
    def __init__(self, body):
        self._body = body

    def get(self, url, headers=None):
        return HttpResponse(200, self._body, {})

    def post(self, *a, **k):
        return HttpResponse(404, {}, {})


class _TextTransport:
    def __init__(self, text):
        self._text = text

    def get(self, url, headers=None):
        return HttpResponse(200, {}, {}, text=self._text)

    def post(self, *a, **k):
        return HttpResponse(404, {}, {})


class _BoomTransport:
    def get(self, url, headers=None):
        raise RuntimeError("réseau coupé")

    def post(self, *a, **k):
        raise RuntimeError


# -- Fear & Greed ------------------------------------------------------------

def test_fear_greed_contrarian_bias():
    fear = FearGreedProvider(transport=_JsonTransport({"data": [{"value": "10", "value_classification": "Extreme Fear"}]}))
    sig = fear.evaluate()
    assert sig.bias > 0  # peur → biais positif (contrarien)
    assert sig.risk_off is False


def test_fear_greed_extreme_greed_triggers_risk_off():
    greed = FearGreedProvider(transport=_JsonTransport({"data": [{"value": "95", "value_classification": "Extreme Greed"}]}))
    sig = greed.evaluate()
    assert sig.bias < 0
    assert sig.risk_off is True


def test_fear_greed_network_error_is_neutral():
    sig = FearGreedProvider(transport=_BoomTransport()).evaluate()
    assert sig.bias == 0.0 and sig.risk_off is False


# -- News --------------------------------------------------------------------

def test_news_risk_off_on_many_risk_titles():
    xml = "<title>Feed</title>" + "".join(
        f"<title>Major exchange hack, funds stolen #{i}</title>" for i in range(4)
    )
    news = NewsProvider(transport=_TextTransport(xml), feeds=["x"], risk_off_hits=3)
    sig = news.evaluate()
    assert sig.risk_off is True


def test_news_quiet_is_neutral():
    xml = "<title>Feed</title><title>Bitcoin price steady as markets calm</title>"
    news = NewsProvider(transport=_TextTransport(xml), feeds=["x"], risk_off_hits=3)
    sig = news.evaluate()
    assert sig.risk_off is False


# -- CoinGecko ---------------------------------------------------------------

def test_coingecko_positive_change_positive_score():
    body = [{"id": "bitcoin", "price_change_percentage_24h": 8.0}]
    cg = CoinGeckoSentimentProvider(transport=_JsonTransport(body), full_swing_pct=8.0)
    scores = cg.scores_for(["BTC", "ETH"])
    assert scores["BTC"].score == 1.0
    assert scores["ETH"].score == 0.0  # absent → neutre


# -- Composites --------------------------------------------------------------

def test_composite_sentiment_weights_by_confidence():
    a = FakeSentimentProvider()
    a.set("BTC", 1.0, mentions=10, confidence=1.0)
    b = FakeSentimentProvider()
    b.set("BTC", -1.0, mentions=0, confidence=0.0)  # neutre, ne doit pas peser
    comp = CompositeSentimentProvider([a, b])
    assert comp.scores_for(["BTC"])["BTC"].score == 1.0


def test_composite_market_risk_off_if_any():
    calm = NullMarketProvider()

    class _RiskOff:
        def evaluate(self):
            return MarketSignal(bias=-0.2, risk_off=True, reasons=["boom"])

    comp = CompositeMarketProvider([calm, _RiskOff()])
    assert comp.evaluate().risk_off is True
