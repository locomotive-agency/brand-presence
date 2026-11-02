"""Saved Peec answers and captured mappings, with invented wire values only."""
import copy
import json
from datetime import UTC, date, datetime

import pytest

from brand_presence import status
from brand_presence.client_ref import BenchmarkSet, ClientRef, Location
from brand_presence.runs import Period
from brand_presence.settings import Brand, PeecBrand, PeecProject
from brand_presence.sources.base import Answer, Request, stored_request
from brand_presence.sources.peec import PeecAnswer, PeecAnswerBrand, PeecPull, PeecRaw, PeecSource


@pytest.fixture
def asked(settings):
    asked, = PeecSource().plan(settings, Period("2026-W10", date(2026, 3, 2)))
    return Request({**asked.body, "vendor_prompt_id": "sample-prompt-reference",
        "prompt_id": "sample-prompt", "prompt_version": "sample-version"}, asked.targets)


def response(*, total=1):
    listings, details = [], []
    for number in range(total):
        common = {"id": "sample-chat-" + str(number), "prompt": {"id": "sample-prompt-reference"},
                  "model_channel": {"id": "openai-0"}, "model": {"id": "sample-model"}}
        listings.append({**common, "date": "2026-03-03"})
        details.append({**common, "messages": [{"role": "assistant", "content":
            "Aardvark Sample is an invented brand."}], "brands_mentioned": [], "sources": []})
    return {"parts": {"chats": [{"data": listings, "total_count": total}], "contents": details}}


def raw(asked, settings, body):
    return PeecRaw(run_id="sample-run", request_key="sample-key", period="2026-W10",
        content_date=None, pulled_at=datetime(2026, 4, 2, tzinfo=UTC),
        request=stored_request(asked, settings.load_number, settings.snapshot_id),
        response=json.dumps(body), usable=True, load_number=settings.load_number,
        snapshot_id=settings.snapshot_id)


def overview_week(asked, engine="ai-overview", content=""):
    from brand_presence.vendors.peec import CHANNELS

    asked = Request({**asked.body, "engine": engine}, asked.targets)
    body = response(total=7)
    for number, (listing, detail) in enumerate(zip(
        body["parts"]["chats"][0]["data"], body["parts"]["contents"], strict=True
    )):
        listing["date"] = f"2026-03-{number + 2:02d}"
        listing["model_channel"] = detail["model_channel"] = {"id": CHANNELS[engine]}
        detail.update(messages=[{"role": "user", "content": "An invented question"},
            {"role": "assistant", "content": "An invented answer. " * 150}],
            sources=[{"url": "https://sample.example/page", "citation_count": 1}],
            links=[], features=[])
    body["parts"]["contents"][-1].update(
        messages=[{"role": "user", "content": "An invented question"},
                  {"role": "assistant", "content": content}], sources=[])
    return asked, body


@pytest.mark.parametrize("content", ["", " \n\t "])
def test_no_overview_keeps_complete_week_without_brand_or_citation_rows(settings, asked, content):
    from brand_presence.sources.ai_answers import NONE_SHOWN

    asked, body = overview_week(asked, content=content)
    source = PeecSource()
    assert source.complete(asked, Answer(body)).usable
    parsed = source.parse(raw(asked, settings, body), settings)
    answers = [r for r in parsed.rows if isinstance(r, PeecAnswer)]
    assert len(answers) == 7
    absent, = [r for r in answers if r.state == NONE_SHOWN]
    assert absent.answer_text == ""
    assert absent.citation_known and absent.cited_links == absent.cited_domains == []
    assert not any(isinstance(r, PeecAnswerBrand) and r.answer_key == absent.answer_key
                   for r in parsed.rows)
    assert sum(r.state == "answered" for r in answers) == 6
    pull, = [r for r in parsed.rows if isinstance(r, PeecPull)]
    assert pull.complete_snapshot and pull.chats_observed == 7
    assert parsed.counts["peec_no_overview"] == 1


@pytest.mark.parametrize("engine,fault", [
    ("ai-overview", "sources"), ("ai-overview", "links"),
    ("ai-overview", "brands_mentioned"),
    *[("ai-overview", f"{field}-{shape}")
      for field in ("sources", "links", "brands_mentioned")
      for shape in ("missing", "null", "object")],
    ("ai-overview", "no-assistant"), ("ai-overview", "multiple"),
    ("ai-overview", "missing-content"), ("ai-overview", "null-content"),
])
def test_unverified_empty_answers_stay_unknown(asked, engine, fault):
    asked, body = overview_week(asked, engine)
    detail = body["parts"]["contents"][-1]
    if fault in ("sources", "links", "brands_mentioned"):
        detail[fault] = [{"url": "https://sample.example/page", "id": "sample-brand"}]
    elif fault and fault.endswith("-missing"):
        del detail[fault.removesuffix("-missing")]
    elif fault and fault.endswith("-null"):
        detail[fault.removesuffix("-null")] = None
    elif fault and fault.endswith("-object"):
        detail[fault.removesuffix("-object")] = {}
    elif fault == "no-assistant":
        detail["messages"].pop()
    elif fault == "multiple":
        detail["messages"].append({"role": "assistant", "content": ""})
    elif fault == "missing-content":
        del detail["messages"][-1]["content"]
    elif fault == "null-content":
        detail["messages"][-1]["content"] = None
    elif fault == "whitespace":
        detail["messages"][-1]["content"] = " \n\t "
    verdict = PeecSource().complete(asked, Answer(body))
    assert not verdict.usable and verdict.rule == status.PEEC_ANSWER_STATE_UNKNOWN




def test_parse_retains_same_day_chats_and_observation_month(settings, asked):
    parsed = PeecSource().parse(raw(asked, settings, response(total=2)), settings)
    answers = [r for r in parsed.rows if isinstance(r, PeecAnswer)]
    assert len(answers) == len({r.answer_key for r in answers}) == 2
    assert {r.observation_date for r in answers} == {date(2026, 3, 3)}
    assert all(r.pulled_at.month == 4 for r in answers)
    assert all(r.benchmark_version == settings.benchmark_sets[0].version for r in answers)


def test_empty_pull_has_marker_and_no_absence(settings, asked):
    parsed = PeecSource().parse(raw(asked, settings, response(total=0)), settings)
    pull, = parsed.rows
    assert isinstance(pull, PeecPull)
    assert pull.complete_snapshot and pull.chats_observed == pull.rows_written == 0


@pytest.mark.parametrize("fault,expected", [
    ("duplicate", status.PEEC_PAGINATION_INCOMPLETE),
    ("missing", status.PEEC_PAGINATION_INCOMPLETE),
    ("total", status.PEEC_PAGINATION_INCOMPLETE),
    ("date", status.PEEC_ANSWER_DATE_INVALID),
    ("outside", status.PEEC_ANSWER_DATE_INVALID),
    ("channel", status.PEEC_ENGINE_UNMAPPED),
    ("prompt", status.PEEC_ANSWER_MISMATCH),
    ("empty", status.PEEC_ANSWER_STATE_UNKNOWN),
    ("multiple", status.PEEC_ANSWER_STATE_UNKNOWN),
])
def test_incomplete_or_unusable_request_cannot_publish(asked, fault, expected):
    body = response(total=2)
    parts = body["parts"]
    listing = parts["chats"][0]
    detail = parts["contents"][0]
    if fault == "duplicate":
        listing["data"][1] = copy.deepcopy(listing["data"][0])
    elif fault == "missing":
        parts["contents"].pop()
    elif fault == "total":
        listing["total_count"] = 3
    elif fault in ("date", "outside"):
        listing["data"][0]["date"] = "unknown" if fault == "date" else "2026-04-01"
    elif fault == "channel":
        listing["data"][0]["model_channel"] = {"id": "sample-api-channel"}
    elif fault == "prompt":
        detail["prompt"] = {"id": "sample-other-prompt"}
    elif fault == "empty":
        detail["messages"] = []
    else:
        detail["messages"] *= 2
    verdict = PeecSource().complete(asked, Answer(body))
    assert not verdict.usable and verdict.rule == expected


@pytest.mark.parametrize("value,count", [(None, 0), ("bad", 1), (True, 1), (-1, 1), (9, 0)])
def test_vendor_position_stays_separate_and_null_is_not_malformed(settings, asked, value, count):
    body = response()
    brand = settings.brand(settings.benchmark_sets[0].client_brand_id)
    body["parts"]["contents"][0]["brands_mentioned"] = [
        {"id": "sample-" + brand.brand_id, "position": value}]
    parsed = PeecSource().parse(raw(asked, settings, body), settings)
    row = next(r for r in parsed.rows if isinstance(r, PeecAnswerBrand)
               and r.subject_brand_id == brand.brand_id)
    assert row.vendor_mentioned is True
    assert row.vendor_position == (9 if value == 9 else None)
    assert row.mention_rank == 1
    assert parsed.counts.get("values_unreadable", 0) == count


@pytest.mark.parametrize("sources,known,counter", [
    ([], True, None), (None, False, "peec_citations_unknown"),
    ([{"url": "https://sample.example/page"}], False, "peec_citations_unknown"),
    ([{"url": "https://sample.example/page", "citation_count": 0}], True, None),
    ([{"url": "", "citation_count": 1}], False, "links_unreadable"),
])
def test_citation_unknown_is_not_zero(settings, asked, sources, known, counter):
    body = response()
    body["parts"]["contents"][0]["sources"] = sources
    parsed = PeecSource().parse(raw(asked, settings, body), settings)
    row = next(r for r in parsed.rows if isinstance(r, PeecAnswer))
    assert row.citation_known is known and row.cited_links == []
    if counter:
        assert parsed.counts[counter] == 1


def test_only_explicit_sources_are_planned(settings):
    period = Period("2026-W10", date(2026, 3, 2))
    assert PeecSource().plan(replace(settings, peec_projects=()), period) == []




def test_page_cap_refuses_the_whole_snapshot(asked):
    from brand_presence.sources.peec import MAX_PAGES

    body = response()
    body["parts"]["chats"][0]["total_count"] = MAX_PAGES + 1
    verdict = PeecSource().complete(asked, Answer(body))
    assert not verdict.usable and verdict.rule == status.PEEC_PAGE_CAP


def test_unknown_citation_does_not_discard_readable_text_or_links(settings, asked):
    body = response()
    body["parts"]["contents"][0]["sources"] = [
        {"url": "https://sample.example/page", "citation_count": 1},
        {"url": "https://sample.example/unknown"}]
    parsed = PeecSource().parse(raw(asked, settings, body), settings)
    answer = next(row for row in parsed.rows if isinstance(row, PeecAnswer))
    assert answer.answer_text == body["parts"]["contents"][0]["messages"][0]["content"]
    assert answer.cited_links == ["https://sample.example/page"]
    assert not answer.citation_known
    assert all(row.cited is None for row in parsed.rows if isinstance(row, PeecAnswerBrand))
    assert parsed.counts["peec_citations_unknown"] == 1




@pytest.mark.parametrize("fault,expected", [
    ("project", status.PEEC_PROJECT_ACCESS),
    ("country", status.PEEC_MAPPING_INVALID),
    ("brand", status.PEEC_MAPPING_INVALID),
    ("channel", status.PEEC_ENGINE_UNMAPPED),
])
def test_metadata_drift_stops_before_any_chat(settings, asked, monkeypatch, fault, expected):
    from collections import Counter
    from types import SimpleNamespace

    from brand_presence.sources.peec import PeecSend

    project = settings.peec_projects[0]
    projects = [{"id": project.project_id, "country_code": project.country_code}]
    brands = [{"id": b.vendor_brand_id, "is_own": b.brand_id == project.client_brand_id}
              for b in settings.peec_brands]
    channels = [{"id": "openai-0", "is_active": True, "unsupported_country_codes": []}]
    if fault == "project":
        projects.clear()
    elif fault == "country":
        projects[0]["country_code"] = "ZZ"
    elif fault == "brand":
        brands[0]["is_own"] = not brands[0]["is_own"]
    elif fault == "channel":
        channels[0]["is_active"] = False
    sender = object.__new__(PeecSend)
    sender.ref, sender.bq, sender.bound, sender._counts = settings, None, {}, Counter()
    sender.api = SimpleNamespace(projects=lambda: projects, brands=lambda _: brands,
        channels=lambda _: channels)
    assert sender.check(project.mapping, [asked]) == expected
    assert sender.bound == {}


@pytest.mark.parametrize("engine,channel", [
    ("chatgpt", "openai-0"),
    ("perplexity", "perplexity-0"),
    ("gemini", "google-2"),
    ("ai-overview", "google-0"),
])
@pytest.mark.parametrize("availability", ["active", "inactive", "missing"])
def test_verified_engine_channels(settings, asked, engine, channel, availability):
    from collections import Counter
    from types import SimpleNamespace

    from brand_presence.sources.peec import PeecSend
    from brand_presence.vendors.peec import CHANNELS

    assert CHANNELS[engine] == channel
    project = settings.peec_projects[0]
    channels = [] if availability == "missing" else [
        {"id": channel, "is_active": availability == "active"}]
    sender = object.__new__(PeecSend)
    sender.ref, sender.bq, sender.bound, sender._counts = settings, None, {}, Counter()
    sender.api = SimpleNamespace(
        projects=lambda: [{"id": project.project_id, "country_code": project.country_code}],
        brands=lambda _: [
            {"id": b.vendor_brand_id, "is_own": b.brand_id == project.client_brand_id}
            for b in settings.peec_brands],
        channels=lambda _: channels)
    request = Request({**asked.body, "engine": engine}, asked.targets)
    expected = None if availability == "active" else "peec engine unmapped"
    assert sender.check(project.mapping, [request]) == expected


def test_duplicate_and_unmapped_mentions_are_counted_before_fanout(settings, asked):
    body = response()
    member = settings.peec_brands[0]
    body["parts"]["contents"][0]["brands_mentioned"] = [
        {"id": member.vendor_brand_id, "position": 2},
        {"id": member.vendor_brand_id, "position": 3},
        {"id": "sample-outside-benchmark", "position": 1}]
    parsed = PeecSource().parse(raw(asked, settings, body), settings)
    brands = [r for r in parsed.rows if isinstance(r, PeecAnswerBrand)]
    assert {r.subject_brand_id for r in brands} == {b.brand_id for b in settings.peec_brands}
    assert next(r for r in brands if r.subject_brand_id == member.brand_id).vendor_position is None
    assert parsed.counts["values_unreadable"] == parsed.counts["peec_brands_unmapped"] == 1


def test_absent_mentions_remain_unknown_and_links_keep_first_seen_order(settings, asked):
    body = response()
    detail = body["parts"]["contents"][0]
    del detail["brands_mentioned"]
    detail["sources"] = [{"url": url, "citation_count": 1} for url in
        ("https://sample.example/z", "https://sample.example/a", "https://sample.example/z")]
    detail["links"] = [{"url": "https://sample.example/not-a-citation"}]
    parsed = PeecSource().parse(raw(asked, settings, body), settings)
    answer = next(r for r in parsed.rows if isinstance(r, PeecAnswer))
    assert answer.cited_links == ["https://sample.example/z", "https://sample.example/a"]
    assert answer.citation_known
    assert all(r.vendor_mentioned is None and r.vendor_position is None
               for r in parsed.rows if isinstance(r, PeecAnswerBrand))
    assert not parsed.counts




@pytest.mark.parametrize("engine", ["chatgpt", "gemini", "perplexity"])
def test_live_week_omits_only_empty_ad_chat(settings, asked, engine):
    asked, body = overview_week(asked, engine)
    for detail in body["parts"]["contents"][:-1]:
        detail["messages"][-1]["content"] = "Invented assistant text. " * 30
        detail["sources"] = [{"url": f"https://sample.example/{n}", "citation_count": 1}
                             for n in range(12)]
    body["parts"]["contents"][-1]["features"] = ["AD"]
    source = PeecSource()
    assert source.complete(asked, Answer(body)).usable
    parsed = source.parse(raw(asked, settings, body), settings)
    answers = [r for r in parsed.rows if isinstance(r, PeecAnswer)]
    assert len(answers) == 6
    assert all(r.state == "answered" and len(r.cited_links) == 12 for r in answers)
    assert {r.answer_key for r in parsed.rows if isinstance(r, PeecAnswerBrand)} == {
        r.answer_key for r in answers}
    pull, = [r for r in parsed.rows if isinstance(r, PeecPull)]
    assert pull.complete_snapshot and pull.chats_observed == 7
    assert parsed.counts["peec_chats_no_text"] == 1
    assert "peec_no_overview" not in parsed.counts


@pytest.mark.parametrize("engine", ["chatgpt", "gemini", "perplexity"])
@pytest.mark.parametrize("fault", ["sources", "links", "brands_mentioned",
    "sources-missing", "links-null", "brands_mentioned-object", "no-assistant",
    "multiple", "missing-content", "null-content", "whitespace"])
def test_non_overview_malformed_empty_chat_still_refuses(asked, engine, fault):
    test_unverified_empty_answers_stay_unknown(asked, engine, fault)








def replace(model, **values):
    return model.model_copy(update=values)


@pytest.fixture
def settings():
    own = Brand(brand_id="aardvark", name="Aardvark Sample", domains=["aardvark.example"])
    rival = Brand(brand_id="rival-capybara", name="Capybara Sample", domains=["capybara.example"])
    other = Brand(brand_id="rival-dormouse", name="Dormouse Sample", domains=["dormouse.example"])
    project = PeecProject(mapping="sample-mapping", project_id="example-project",
        client_brand_id="aardvark", country_code="NL", engines=["chatgpt"],
        first_date=date(2026, 3, 1))
    return ClientRef(load_number=1, snapshot_id="example-snapshot", brands=(own,rival,other),
        locations=(Location(location_id="nl", country_code="NL", canonical_name="Netherlands",
                            sources=("peec",)),),
        benchmark_sets=(BenchmarkSet(client_brand_id="aardvark", country_code="NL", version=1,
                        language="nl", keywords=(), competitors=(rival,other)),),
        peec_projects=(project,), peec_brands=tuple(PeecBrand(mapping=project.mapping,
            brand_id=b.brand_id, vendor_brand_id="sample-"+b.brand_id) for b in (own,rival,other)))
