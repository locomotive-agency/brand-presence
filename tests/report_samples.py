"""Invented report fixtures from the source suite."""
from __future__ import annotations

from datetime import UTC, date, datetime

from brand_presence.reports.definition import Report, load
from report_sql import run_statement

THEN = datetime(2026, 3, 2, 12, tzinfo=UTC)
TODAY = date(2026, 3, 2)
JANUARY = date(2026, 1, 1)
AI_PERIOD = "2026-W09"
AI_PERIOD_START = date(2026, 2, 23)
def place(location: str, kind: str) -> dict:
    return {"load_number": 2, "location_id": location, "country_code": "NL", "kind": kind,
            "name": location, "canonical_name": location, "region_code": None,
            "sources": ["search-volume"], "google_criterion_id": None, "latitude": None,
            "longitude": None}

def brand(brand_id: str, kind: str) -> dict:
    return {"load_number": 2, "brand_id": brand_id, "name": brand_id.title(), "kind": kind,
            "domains": [], "aliases": []}

def competitor(brand_id: str, version: int = 2) -> dict:
    return {"load_number": 2, "client_brand_id": "sample", "country_code": "NL",
            "benchmark_version": version, "brand_id": brand_id}

def ai_answer(prompt: str = "sample-prompt", text: str | None = "Shoes.", *,
              state: str = "answered", links: tuple[str, ...] = (), place: str = "sample-nl",
              answered: datetime = THEN, version: int | None = 2) -> dict:
    """A row of stg.ai_answers: one answer."""
    return {"client_brand_id": "sample", "country_code": "NL", "location_id": place,
            "benchmark_version": version, "snapshot_id": "snapshot-2", "period": AI_PERIOD,
            "period_start": AI_PERIOD_START, "request_key": "sample-request",
            "run_id": "sample-run", "pulled_at": answered, "prompt_id": prompt,
            "engine": "sample-engine", "state": state,
            "answer_text": text if state == "answered" else None, "model": None,
            "cited_domains": [], "cited_links": list(links)}

def ai_brand(brand_id: str, at: int | None = None, rank: int | None = None, *,
             cited: bool = False, prompt: str = "sample-prompt", place: str = "sample-nl",
             answered: datetime = THEN) -> dict:
    """A row of stg.ai_answer_brands: one brand of an answer's set, named where `at` is
    given, at that many characters into the answer."""
    return {"client_brand_id": "sample", "country_code": "NL", "location_id": place,
            "benchmark_version": 2, "snapshot_id": "snapshot-2", "period": AI_PERIOD,
            "period_start": AI_PERIOD_START, "request_key": "sample-request",
            "run_id": "sample-run", "pulled_at": answered, "prompt_id": prompt,
            "engine": "sample-engine", "subject_brand_id": brand_id, "named": at is not None,
            "cited": cited, "first_mention_at": at, "mention_rank": rank}

def ai_fact(name: str, answers: list[dict], brands: list[dict] | None = None, *,
            report: Report | None = None, pulled_at: datetime = THEN) -> list[dict]:
    """The rows a build of an AI answers fact appends, each with the build's pulled_at. The
    stand-in cannot read an empty table's columns, so where no answer cites a link the links'
    fact is given as empty, as its statement returns no row then."""
    report = report or {r.name: r for r in load()}[name]
    if name == "fct_ai_cited_links_v1" and not any(a["cited_links"] for a in answers):
        return []
    tables = {"stg.ai_answers": answers}
    if name == "fct_ai_answers_v1":
        tables["stg.ai_answer_brands"] = brands or []
    return [{**r, "pulled_at": pulled_at} for r in run_statement(report, tables, today=TODAY)]

def ai_tables(answers: list[dict], brands: list[dict], **given: list[dict]
              ) -> dict[str, list[dict]]:
    """The tables ai_prompt_results_v2 reads: the two facts built from the source rows given,
    the set of version 2 and today's tabs."""
    tables = {
        "core.fct_ai_answers_v1": ai_fact("fct_ai_answers_v1", answers, brands),
        "core.fct_ai_cited_links_v1": ai_fact("fct_ai_cited_links_v1", answers),
        "ref.benchmark_competitors": [competitor("rival"), competitor("other"),
                                      competitor("gone", version=1)],
        "ref.current_brands": [brand("sample", "client"), brand("rival", "competitor"),
                               brand("other", "competitor"), brand("gone", "competitor")],
        "ref.current_benchmark_prompts": [{
            "load_number": 2, "client_brand_id": "sample", "country_code": "NL",
            "benchmark_version": 2, "prompt_id": "sample-prompt", "prompt": "Which shoes?",
            "engines": ["sample-engine"], "location_ids": [], "keyword": "running shoes",
            "keyword_group": "shoes", "intent": "comparison"}],
        "ref.current_locations": [{**place("sample-nl", "country"),
                                   "sources": ["search-volume", "ai-answers"]}],
    }
    return {**tables, **{t.replace("__", "."): rows for t, rows in given.items()}}

def result_row(run: str, prompt: str, brand_id: str, mentioned: bool | None,
               rank: int | None = None, *, keyword: str | None = "running shoes",
               cited: bool | None = False, state: str = "answered") -> dict:
    """A row of mart.ai_prompt_results_v2."""
    return {"run_id": run, "run_date": date(2026, 1, 13), "month": JANUARY,
            "client_brand_id": "sample", "country_code": "NL", "prompt_id": prompt,
            "keyword": keyword, "keyword_group": "shoes" if keyword else None,
            "intent": "comparison" if keyword else None, "engine": "sample-engine",
            "geo_id": "sample-nl", "geo_level": "national", "brand": brand_id,
            "brand_name": brand_id.title(), "brand_set_version": 2,
            "answer_state": state, "brand_mentioned": mentioned, "mention_rank": rank,
            "is_recommended": None, "sentiment": None, "client_domain_cited": cited,
            "cited_urls": [], "answer_snippet": None, "prompt_set_version": 2,
            "pulled_at": THEN}

def citation_tables(rows: list[dict]) -> dict[str, list[dict]]:
    """The tables ai_citations_v1 reads. Version 1 of the set was made at load 1, with the
    competitor gone; version 2 at load 2, with the rival, and still in force at load 3. At
    load 3 the client brand's and the rival's domains changed; an answer of version 2 is
    marked with load 2's."""
    def brands(load: int, own: str, rival: str) -> list[dict]:
        return [{"load_number": load, "brand_id": "sample", "domains": [own]},
                {"load_number": load, "brand_id": "rival", "domains": [rival]},
                {"load_number": load, "brand_id": "gone", "domains": ["gone.example"]}]

    def version(load: int, number: int) -> dict:
        return {"load_number": load, "client_brand_id": "sample", "country_code": "NL",
                "benchmark_version": number, "content_hash": f"sample-hash-{number}"}

    return {
        "mart.ai_prompt_results_v2": rows,
        "ref.brands": (brands(1, "sample.example", "https://www.rival.example/")
                       + brands(2, "sample.example", "https://www.rival.example/")
                       + brands(3, "sample-new.example", "rival-new.example")),
        "ref.benchmark_sets": [version(1, 1), version(2, 2), version(3, 2)],
        "ref.benchmark_competitors": [{**competitor("gone", version=1), "load_number": 1},
                                      {**competitor("rival"), "load_number": 2}],
    }
