from common.http_transport import HttpResponse
from sentiment.models import SentimentScore
from sentiment.provider import NullSentimentProvider
from sentiment.reddit import RedditSentimentProvider, _post_polarity
from sentiment.testing import FakeSentimentProvider


class _FakeRedditTransport:
    """Sert des messages Reddit factices au format des pages .json publiques."""

    def __init__(self, titles: list[str]):
        self._titles = titles

    def get(self, url, headers=None):
        children = [{"data": {"title": t, "selftext": ""}} for t in self._titles]
        return HttpResponse(200, {"data": {"children": children}}, {})

    def post(self, url, headers=None, json=None, data=None):
        return HttpResponse(404, {}, {})


def test_post_polarity_bounds():
    assert _post_polarity("moon bull rally gains") == 1.0
    assert _post_polarity("crash dump scam rug") == -1.0
    assert _post_polarity("le chat dort") == 0.0
    assert _post_polarity("bull bear") == 0.0


def test_null_provider_is_neutral():
    scores = NullSentimentProvider().scores_for(["BTC", "ETH"])
    assert scores["BTC"].score == 0.0
    assert scores["BTC"].confidence == 0.0
    assert scores["BTC"].is_reliable is False


def test_reddit_provider_scores_positive_when_bullish():
    transport = _FakeRedditTransport([
        "Bitcoin to the moon, huge rally incoming",
        "BTC bull run, buying more, bullish gains",
        "ethereum looks bearish, might dump",
    ])
    provider = RedditSentimentProvider(transport=transport, subreddits=["CryptoCurrency"], confidence_full_at=2)
    scores = provider.scores_for(["BTC", "ETH"])
    assert scores["BTC"].score > 0
    assert scores["BTC"].mentions == 2
    assert scores["BTC"].is_reliable is True
    assert scores["ETH"].score < 0


def test_reddit_provider_never_crashes_on_network_error():
    class _Boom:
        def get(self, url, headers=None):
            raise RuntimeError("réseau coupé")

        def post(self, *a, **k):
            raise RuntimeError

    provider = RedditSentimentProvider(transport=_Boom(), subreddits=["X"])
    scores = provider.scores_for(["BTC"])
    assert scores["BTC"].score == 0.0  # retombe sur neutre, ne lève pas


def test_fake_provider_helper():
    fake = FakeSentimentProvider()
    fake.set("SOL", -0.8)
    scores = fake.scores_for(["SOL", "BTC"])
    assert scores["SOL"].score == -0.8
    assert scores["BTC"] == SentimentScore.neutral("BTC")
