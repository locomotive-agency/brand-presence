"""Register another task source here, with its row models and tests."""
from brand_presence.sources.ai_answers import AiAnswersSource
from brand_presence.sources.mentions import MentionsSource
from brand_presence.sources.peec import PeecSource
from brand_presence.sources.search_volume import SearchVolumeSource

SOURCES = {"search-volume": SearchVolumeSource, "ai-answers": AiAnswersSource,
           "mentions": MentionsSource, "peec": PeecSource}
