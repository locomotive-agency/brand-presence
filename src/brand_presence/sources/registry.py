"""Register another task source here, with its row models and tests."""
from brand_presence.sources.ai_answers import AiAnswersSource
from brand_presence.sources.ga4 import Ga4
from brand_presence.sources.google_ads import GoogleAds
from brand_presence.sources.mentions import MentionsSource
from brand_presence.sources.peec import PeecSource
from brand_presence.sources.search_console import SearchConsole
from brand_presence.sources.search_volume import SearchVolumeSource

SOURCES = {"search-volume": SearchVolumeSource, "ai-answers": AiAnswersSource,
           "mentions": MentionsSource, "peec": PeecSource,
           "search-console": SearchConsole, "ga4": Ga4, "google-ads": GoogleAds}
