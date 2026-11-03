"""Known-result serving report cases ported from the source suite."""
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from brand_presence.reports.definition import Report, load
from report_sql import run_statement as execute


def run_statement(report, tables, **kwargs):
    from brand_presence.rows import columns_of
    from brand_presence.schema import MODELS

    schemas = {m.table: {n: kind for n, (kind, _) in columns_of(m).items()} for m in MODELS}
    schemas.update({name.replace('ref.', 'ref.current_'): cols
                    for name, cols in list(schemas.items()) if name.startswith('ref.')})
    schemas.update({r.table: {c['name']: c['type'] for c in r.columns} for r in load()})
    return execute(report, tables, schemas=schemas, **kwargs)

THEN = datetime(2026, 3, 2, 12, tzinfo=UTC)
EARLIER = datetime(2026, 2, 2, 12, tzinfo=UTC)
TODAY = date(2026, 3, 2)
JANUARY = date(2026, 1, 1)


def volume(keyword: str, searches: int | None, month: date = JANUARY, place: str = "sample-nl",
           pulled_at: datetime = THEN) -> dict:
    return {"month": month, "client_brand_id": "sample", "country_code": "NL",
            "location_id": place, "keyword": keyword, "term_type": "brand",
            "subject_brand_id": None, "benchmark_version": 1, "search_volume": searches,
            "pulled_at": pulled_at}


def keyword(text: str, term_type: str, brand: str | None, canonical: str | None = None,
            version: int = 2) -> dict:
    return {"load_number": 2, "client_brand_id": "sample", "country_code": "NL",
            "benchmark_version": version, "keyword": text, "term_type": term_type,
            "brand_id": brand, "search_volume": True, "search_trends": False,
            "search_results": False, "trends_anchor": False, "canonical_term": canonical}


def query(text: str, term_type: str, clicks: int, subject: str | None = None,
          month: date = JANUARY) -> dict:
    return {"month": month, "client_brand_id": "sample", "web_property_id": "sample-site",
            "country_code": "NL", "search_type": "web", "row_kind": "query", "query": text,
            "term_type": term_type, "subject_brand_id": subject, "clicks": clicks,
            "impressions": clicks * 10, "position_weighted": clicks * 20, "days_covered": 31,
            "pulled_at": THEN}


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


def sample_tables(**given: list[dict]) -> dict[str, list[dict]]:
    tables = {
        "core.fct_search_volume_v1": [
            volume("sample shoes", 100), volume("sampel shoes", 10),
            volume("rival shoes", 300), volume("running shoes", 1000),
            volume("sample shoes", 40, place="sample-nl-region"),
            volume("sample shoes", 80, month=date(2025, 1, 1)),
        ],
        "core.fct_gsc_export_v1": [],
        "stg.search_console_pulls": [],
        "core.fct_gsc_v1": [query("sample shoes", "brand", 5, subject="sample")],
        "ref.current_benchmark_keywords": [
            keyword("sample shoes", "brand", "sample"),
            keyword("sampel shoes", "brand", None, canonical="sample shoes"),
            keyword("rival shoes", "competitor", "rival"),
            keyword("running shoes", "generic", None),
        ],
        "ref.current_benchmark_competitors": [competitor("rival")],
        "ref.current_locations": [place("sample-nl", "country"),
                                  place("sample-nl-region", "region")],
        "ref.current_brands": [brand("sample", "client"), brand("rival", "competitor")],
        "ref.current_web_properties": [{
            "load_number": 2, "web_property_id": "sample-site", "client_brand_id": "sample",
            "source": "search-console", "address": "sample-site", "hostname": None}],
    }
    return {**tables, **{t.replace("__", "."): rows for t, rows in given.items()}}


def demand(tables: dict[str, list[dict]], report: Report | None = None) -> list[dict]:
    report = report or {r.name: r for r in load()}["search_demand_monthly_v1"]
    return run_statement(report, tables, today=TODAY)


def rewritten(name: str, old: str, new: str) -> Report:
    report = {r.name: r for r in load()}[name]
    assert report.statement.count(old) == 1, old
    return replace(report, statement=report.statement.replace(old, new))


def test_a_national_row_and_a_state_row():
    rows = demand(sample_tables())
    levels = {(r["geo_id"], r["geo_level"]) for r in rows}
    assert levels == {("sample-nl", "national"), ("sample-nl-region", "state")}
    key = {r.name: r for r in load()}["search_demand_monthly_v1"].definition["key"]
    assert all(r[k] is not None for r in rows for k in key)
    region = [r for r in rows if r["geo_level"] == "state"]
    assert [(r["brand"], r["search_volume"]) for r in region] == [("sample", 40)]


def category_clicks(rows: list[dict]) -> int | None:
    (row,) = [r for r in rows if r["term_type"] == "category" and r["geo_level"] == "national"]
    return row["organic_clicks"]


def test_the_category_match_is_whole_words():
    tables = sample_tables(core__fct_gsc_v1=[
        query("best Running Shoes", "generic", 7),
        query("runningshoes", "generic", 9),
        query("running shoesmith", "generic", 11),
        query("sample shoes", "brand", 5, subject="sample"),
    ])
    rows = demand(tables)
    assert category_clicks(rows) == 7
    (own,) = [r for r in rows if r["brand"] == "sample" and r["geo_level"] == "national"
              and r["month"] == JANUARY]
    assert own["organic_clicks"] == 5

    labelled = sample_tables(core__fct_gsc_v1=[query("sample running shoes", "brand", 3,
                                                     subject="sample")])
    assert category_clicks(demand(labelled)) is None

    anywhere = rewritten(
        "search_demand_monthly_v1",
        "STRPOS(CONCAT(' ', LOWER(queries.query), ' '),\n"
        "      CONCAT(' ', LOWER(generic.keyword), ' ')) > 0",
        "STRPOS(LOWER(queries.query), LOWER(generic.keyword)) > 0")
    assert category_clicks(demand(tables, anywhere)) == 7 + 11


def test_share_of_search_uses_todays_set():
    old_set = [competitor("rival", version=1), competitor("dropped", version=1)]
    tables = sample_tables(
        ref__benchmark_competitors=[*old_set, competitor("rival")],
        ref__current_benchmark_keywords=[
            *sample_tables()["ref.current_benchmark_keywords"],
            keyword("dropped shoes", "competitor", "dropped"),
        ],
        core__fct_search_volume_v1=[
            *sample_tables()["core.fct_search_volume_v1"],
            volume("dropped shoes", 600),
        ],
    )

    def problems(rows: list[dict]) -> list[str]:
        found = [f"{r['brand']}: version {r['brand_set_version']}" for r in rows
                 if r["brand_set_version"] != 2]
        found += [f"{r['brand']}: a row" for r in rows if r["brand"] == "dropped"]
        national = [r for r in rows if r["geo_level"] == "national" and r["month"] == JANUARY
                    and r["term_type"] == "brand"]
        found += [f"{r['brand']}: set volume {r['brand_set_volume']}" for r in national
                  if r["brand_set_volume"] != 100 + 10 + 300]
        return found

    rows = demand(tables)
    assert problems(rows) == []
    shares = {r["brand"]: r["share_of_search"] for r in rows
              if r["geo_level"] == "national" and r["month"] == JANUARY}
    assert shares["sample"] == pytest.approx(110 / 410)
    assert shares["rival"] == pytest.approx(300 / 410)
    assert shares["(all)"] is None

    every = rewritten("search_demand_monthly_v1",
                      "FROM ref.current_benchmark_competitors AS competitor",
                      "FROM ref.benchmark_competitors AS competitor")
    assert "dropped: a row" in problems(demand(tables, every))


def test_the_year_ago_figure_and_the_latest_pull():
    tables = sample_tables(core__fct_search_volume_v1=[
        *sample_tables()["core.fct_search_volume_v1"],
        volume("sample shoes", 999, pulled_at=EARLIER),
    ])
    rows = demand(tables)
    (own,) = [r for r in rows if r["brand"] == "sample" and r["geo_level"] == "national"
              and r["month"] == JANUARY]
    assert (own["search_volume"], own["search_volume_year_ago"]) == (110, 80)
    assert own["search_volume_yoy_change"] == pytest.approx((110 - 80) / 80)

    old = sample_tables(core__fct_search_volume_v1=[volume("sample shoes", 5,
                                                           month=date(2023, 12, 1))])
    assert demand(old) == []
    earliest = rewritten("search_demand_monthly_v1",
                         "WHERE terms.month >= DATE '2024-01-01'", "")
    assert demand(old, earliest) != []


def test_a_row_short_of_its_keywords_is_not_complete():
    tables = sample_tables(core__fct_search_volume_v1=[
        *sample_tables()["core.fct_search_volume_v1"],
        volume("sample shoes", 90, month=date(2026, 2, 1)),
        volume("sampel shoes", 9, month=date(2026, 2, 1)),
        volume("rival shoes", None, month=date(2026, 2, 1)),
    ])
    rows = {(r["month"], r["geo_id"], r["brand"]): r for r in demand(tables)}
    national = rows[(JANUARY, "sample-nl", "sample")]
    assert (national["keywords_expected"], national["keywords_found"],
            national["is_complete_month"]) == (2, 2, True)
    region = rows[(JANUARY, "sample-nl-region", "sample")]
    assert (region["keywords_expected"], region["keywords_found"],
            region["is_complete_month"], region["search_volume"]) == (2, 1, False, 40)
    rival = rows[(date(2026, 2, 1), "sample-nl", "rival")]
    assert (rival["keywords_expected"], rival["keywords_found"],
            rival["is_complete_month"]) == (1, 0, False)
    february = rows[(date(2026, 2, 1), "sample-nl", "sample")]
    assert (february["keywords_found"], february["share_of_search"]) == (2, 1.0)

    v1 = rewritten("search_demand_monthly_v1",
                   "    AND terms.keywords_found = expected.keywords_expected\n", "\n")
    assert {(r["month"], r["geo_id"], r["brand"]): r for r in demand(tables, v1)}[
        (JANUARY, "sample-nl-region", "sample")]["is_complete_month"] is True


def demand_row(month: date, searches: int, set_searches: int, complete: bool = True) -> dict:
    return {"month": month, "client_brand_id": "sample", "country_code": "NL",
            "geo_id": "sample-nl", "geo_level": "national", "term_type": "brand",
            "brand": "sample", "brand_name": "Sample", "brand_set_version": 2,
            "search_volume": searches, "brand_set_volume": set_searches,
            "share_of_search": searches / set_searches, "search_volume_year_ago": None,
            "search_volume_yoy_change": None, "organic_clicks": None,
            "organic_impressions": None, "organic_position_weighted": None,
            "organic_avg_position": None, "organic_days_covered": None,
            "is_complete_month": complete, "pulled_at": THEN}


def growth(rows: list[dict], report: Report | None = None) -> dict[tuple, dict]:
    report = report or {r.name: r for r in load()}["growth_v1"]
    found = run_statement(report, {"mart.search_demand_monthly_v1": rows}, today=TODAY)
    return {(r["period_type"], r["period_start"]): r for r in found}


def test_share_over_a_period_is_summed_not_averaged():
    rows = [demand_row(date(2026, 1, 1), 10, 100), demand_row(date(2026, 2, 1), 90, 300)]
    mean = (10 / 100 + 90 / 300) / 2
    found = growth(rows)
    quarter = found[("quarter", date(2026, 1, 1))]
    assert quarter["share_of_search"] == pytest.approx(0.25)
    assert quarter["share_of_search"] != pytest.approx(mean)
    assert (quarter["search_volume"], quarter["brand_set_volume"]) == (100, 400)
    assert (quarter["months_in_period"], quarter["is_partial"]) == (2, True)
    assert quarter["period_label"] == "Q1 2026 (partial)"
    rolling = found[("rolling_3", date(2025, 12, 1))]
    assert rolling["share_of_search"] == pytest.approx(0.25)

    averaged = rewritten(
        "growth_v1", "SUM(demand.brand_set_volume) AS brand_set_volume\n",
        "SUM(demand.brand_set_volume) AS brand_set_volume,\n"
        "    AVG(SAFE_DIVIDE(demand.search_volume, demand.brand_set_volume)) AS mean_share\n")
    averaged = replace(averaged, statement=averaged.statement.replace(
        "  SAFE_DIVIDE(figures.search_volume, figures.brand_set_volume) AS share_of_search,",
        "  figures.mean_share AS share_of_search,"))
    copied = growth(rows, averaged)[("quarter", date(2026, 1, 1))]
    assert copied["share_of_search"] == pytest.approx(mean)
    assert copied["share_of_search"] != pytest.approx(0.25)


def test_growth_periods_prior_and_year_ago():
    rows = [demand_row(date(2025, 2, 1), 20, 200), demand_row(date(2026, 1, 1), 10, 100),
            demand_row(date(2026, 2, 1), 30, 100)]
    found = growth(rows)
    month = found[("month", date(2026, 2, 1))]
    assert (month["prior_search_volume"], month["year_ago_search_volume"]) == (10, 20)
    assert month["search_volume_pop_change"] == pytest.approx((30 - 10) / 10)
    assert month["search_volume_yoy_change"] == pytest.approx((30 - 20) / 20)
    assert month["share_of_search_pop_change"] == pytest.approx(0.3 - 0.1)
    assert month["share_of_search_yoy_change"] == pytest.approx(0.3 - 0.1)
    assert (month["period_end"], month["period_label"], month["is_partial"]) == (
        date(2026, 2, 1), "Feb 2026", False)
    year = found[("year", date(2026, 1, 1))]
    assert (year["period_end"], year["months_in_period"], year["prior_search_volume"]) == (
        date(2026, 12, 1), 2, 20)
    half = found[("half", date(2025, 1, 1))]
    assert (half["period_end"], half["period_label"]) == (date(2025, 6, 1), "H1 2025 (partial)")
    assert found[("rolling_12", date(2025, 3, 1))]["search_volume"] == 40

    late = growth([*rows, demand_row(date(2026, 3, 1), 1, 10, complete=False)])
    assert late[("month", date(2026, 3, 1))]["period_label"] == "Mar 2026 (partial)"
    assert late[("month", date(2026, 2, 1))]["is_partial"] is False

def export_clean(month: date = JANUARY, period_start: date = date(2026, 2, 2),
                 place: str = "sample-nl", channel: str = "(all)", users: int = 100,
                 through: date | None = None) -> dict:
    return {"client_brand_id": "sample", "country_code": "NL", "location_id": place,
            "benchmark_version": 1, "snapshot_id": "s", "period": period_start.isoformat(),
            "period_start": period_start, "request_key": "r", "run_id": "run",
            "pulled_at": THEN, "web_property_id": "sample-site", "month": month,
            "through_date": through or date(2026, 1, 31), "geo_level": "national",
            "channel_group": channel, "sessions": 150, "active_users": users - 10,
            "total_users": users, "new_users": users // 2, "returning_users": users // 4,
            "page_views": 300, "engagement_seconds": 600}


def test_the_export_fact_reads_the_newest_period():
    newer, older = date(2026, 2, 9), date(2026, 2, 2)
    clean = [export_clean(period_start=older, users=90),
             export_clean(period_start=older, channel="Direct", users=40),
             export_clean(period_start=newer, users=100)]
    tables = {"stg.ga4_export_engagement": clean,
              "ref.current_locations": [place("sample-nl", "country")]}
    fact = {r.name: r for r in load()}["fct_ga4_export_v1"]
    found = run_statement(fact, tables, today=TODAY)
    assert [(r["channel_group"], r["total_users"]) for r in found] == [("(all)", 100)]
    oldest = rewritten("fct_ga4_export_v1", "MAX(period_row.period_start)",
                       "MIN(period_row.period_start)")
    assert sorted((r["channel_group"], r["total_users"])
                  for r in run_statement(oldest, tables, today=TODAY)) == [
        ("(all)", 90), ("Direct", 40)]

def ga4_place(location: str, kind: str) -> dict:
    return {**place(location, kind), "sources": ["ga4"]}


def export_fact(prop: str, month: date = JANUARY, place_id: str = "sample-nl",
                level: str = "national", channel: str = "(all)", users: int = 100,
                sessions: int = 150, through: date | None = None,
                period_start: date = date(2026, 2, 2), country: str = "NL",
                pulled_at: datetime = THEN) -> dict:
    last = date(month.year + month.month // 12, month.month % 12 + 1, 1) - timedelta(days=1)
    return {"month": month, "client_brand_id": "sample", "web_property_id": prop,
            "country_code": country, "geo_level": level, "location_id": place_id,
            "channel_group": channel, "through_date": through or last,
            "period_start": period_start, "sessions": sessions,
            "page_views": sessions * 2, "engagement_seconds": sessions * 4,
            "active_users": users - 10, "total_users": users, "new_users": users // 2,
            "returning_users": users // 4, "pulled_at": pulled_at}


def api_fact(prop: str, month: date = JANUARY, place_id: str = "sample-nl",
             level: str = "country", channel: str = "Organic Search", sessions: int = 40,
             days: int = 31, sampled: bool = False, country: str = "NL") -> dict:
    return {"month": month, "client_brand_id": "sample", "web_property_id": prop,
            "country_code": country, "geo_level": level, "location_id": place_id,
            "channel_group": channel, "sessions": sessions, "engaged_sessions": sessions // 2,
            "engagement_seconds": sessions * 3, "event_count": sessions * 5,
            "key_events": 1, "page_views": sessions * 2, "days_covered": days,
            "sampled": sampled, "pulled_at": THEN}


def ga4_property(prop: str) -> dict:
    return {"load_number": 2, "web_property_id": prop, "client_brand_id": "sample",
            "source": "ga4", "address": "x", "hostname": None, "bigquery_export": None}


def ga4_pull(prop: str, report: str, pulled_at: datetime = THEN) -> dict:
    return {"web_property_id": prop, "report": report, "pulled_at": pulled_at}


def ga4_tables(**given: list[dict]) -> dict[str, list[dict]]:
    tables = {
        "core.fct_ga4_export_v1": [],
        "core.fct_ga4_v1": [],
        "core.fct_ga4_export_pages_v1": [],
        "stg.ga4_pulls": [],
        "ref.current_web_properties": [],
        "ref.current_locations": [ga4_place("sample-nl", "country"),
                                  ga4_place("sample-nl-region", "region"),
                                  ga4_place("sample-city", "city")],
        "ref.current_pages": [],
    }
    return {**tables, **{t.replace("__", "."): rows for t, rows in given.items()}}


def engagement(tables: dict[str, list[dict]], report: Report | None = None) -> list[dict]:
    report = report or {r.name: r for r in load()}["engagement_monthly_v1"]
    return run_statement(report, tables, today=TODAY)


DECEMBER = date(2025, 12, 1)


def route_tables() -> dict[str, list[dict]]:
    return ga4_tables(
        ref__current_web_properties=[ga4_property(p) for p in ("export-site", "api-site",
                                                               "moved-site")],
        stg__ga4_pulls=[
            ga4_pull("export-site", "export_engagement"),
            ga4_pull("api-site", "engagement"),
            ga4_pull("moved-site", "engagement", EARLIER),
            ga4_pull("moved-site", "export_engagement"),
        ],
        core__fct_ga4_export_v1=[
            export_fact("export-site"),
            export_fact("export-site", place_id="sample-nl-region", level="state"),
            export_fact("api-site"),
            export_fact("moved-site", DECEMBER), export_fact("moved-site"),
        ],
        core__fct_ga4_v1=[
            api_fact("export-site"),
            api_fact("api-site"), api_fact("api-site", channel="Direct", sessions=10),
            api_fact("api-site", place_id="sample-city", level="city", channel="(all)"),
            api_fact("moved-site"),
        ],
    )


def test_a_propertys_route_decides_its_fact():
    def routes(rows: list[dict]) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for row in rows:
            found.setdefault(row["web_property_id"], set()).add(row["ga4_route"])
        return found

    rows = engagement(route_tables())
    assert routes(rows) == {"export-site": {"export"}, "api-site": {"data_api"},
                            "moved-site": {"export"}}
    moved = sorted((r["month"], r["channel_group"]) for r in rows
                   if r["web_property_id"] == "moved-site")
    assert moved == [(DECEMBER, "(all)"), (JANUARY, "(all)")]
    api = {(r["geo_id"], r["channel_group"]): r for r in rows
           if r["web_property_id"] == "api-site"}
    assert set(api) == {("sample-nl", "Organic Search"), ("sample-nl", "Direct"),
                        ("sample-nl", "(all)"), ("sample-city", "(all)")}
    assert api[("sample-nl", "(all)")]["sessions"] == 50
    assert all(r["total_users"] is None and r["active_users"] is None
               and r["returning_users"] is None for r in api.values())
    assert {r["geo_level"] for r in rows if r["web_property_id"] == "export-site"} == {
        "national", "state"}

    both = rewritten("engagement_monthly_v1", "  WHERE routes.ga4_route = 'data_api'\n",
                     "  WHERE routes.ga4_route IN ('data_api', 'export')\n")
    assert routes(engagement(route_tables(), both)) == {
        "export-site": {"export", "data_api"}, "api-site": {"data_api"},
        "moved-site": {"export", "data_api"}}

    earliest = rewritten("engagement_monthly_v1",
                         "OR pull_times.export_pulled_at >= pull_times.api_pulled_at",
                         "OR pull_times.export_pulled_at <= pull_times.api_pulled_at")
    assert routes(engagement(route_tables(), earliest))["moved-site"] == {"data_api"}


def users_tables() -> dict[str, list[dict]]:
    return ga4_tables(
        ref__current_web_properties=[ga4_property("sample-site")],
        stg__ga4_pulls=[ga4_pull("sample-site", "export_engagement")],
        core__fct_ga4_export_v1=[
            export_fact("sample-site", channel="Organic Search", users=60, sessions=70),
            export_fact("sample-site", channel="Direct", users=50, sessions=60),
            export_fact("sample-site", users=100, sessions=130),
        ],
    )


def test_the_total_row_holds_the_places_users():
    def total(report: Report | None = None) -> dict | None:
        rows = {r["channel_group"]: r for r in engagement(users_tables(), report)}
        return rows.get("(all)")

    row = total()
    assert row is not None
    assert (row["total_users"], row["active_users"], row["returning_users"],
            row["sessions"]) == (100, 90, 25, 130)
    assert row["return_visit_rate"] == 0.25
    assert row["time_on_site_sec"] == 130 * 4 / 100
    assert row["pages_per_session"] == 2.0
    assert (row["ga4_route"], row["sampled"], row["is_complete_month"]) == (
        "export", False, True)


    no_total = rewritten("engagement_monthly_v1",
                         "    AND places.geo_level = export_row.geo_level\n",
                         "    AND places.geo_level = export_row.geo_level\n"
                         "    AND export_row.channel_group != '(all)'\n")
    assert total(no_total) is None
    other = rewritten("engagement_monthly_v1", "    export_row.total_users,\n",
                      "    export_row.active_users AS total_users,\n")
    assert total(other)["total_users"] == 90


def test_the_other_row_is_not_shown():
    tables = ga4_tables(
        ref__current_web_properties=[ga4_property("sample-site")],
        stg__ga4_pulls=[ga4_pull("sample-site", "engagement")],
        core__fct_ga4_v1=[api_fact("sample-site"),
                          api_fact("sample-site", level="city", channel="(all)"),
                          api_fact("sample-site", place_id="sample-city", level="city",
                                   channel="(all)")],
    )
    rows = engagement(tables)
    assert sorted((r["geo_id"], r["geo_level"], r["channel_group"]) for r in rows) == [
        ("sample-city", "city", "(all)"), ("sample-nl", "national", "(all)"),
        ("sample-nl", "national", "Organic Search")]
    assert {r["sessions"] for r in rows if r["geo_id"] == "sample-nl"} == {40}

    levels = rewritten("engagement_monthly_v1",
                       "IF(api_row.geo_level = 'country', 'national', api_row.geo_level)",
                       "api_row.geo_level")
    assert {r["geo_level"] for r in engagement(tables, levels)} == {"city"}


def test_a_month_the_data_api_does_not_cover_is_not_complete():
    tables = ga4_tables(
        ref__current_web_properties=[ga4_property("api-site"), ga4_property("export-site")],
        stg__ga4_pulls=[ga4_pull("api-site", "engagement"),
                        ga4_pull("export-site", "export_engagement")],
        core__fct_ga4_v1=[api_fact("api-site"), api_fact("api-site", DECEMBER, days=30)],
        core__fct_ga4_export_v1=[export_fact("export-site"),
                                 export_fact("export-site", DECEMBER,
                                             through=date(2025, 12, 28))],
    )
    complete = {(r["web_property_id"], r["month"], r["channel_group"]): r["is_complete_month"]
                for r in engagement(tables)}
    assert complete == {
        ("api-site", JANUARY, "Organic Search"): True, ("api-site", JANUARY, "(all)"): True,
        ("api-site", DECEMBER, "Organic Search"): False, ("api-site", DECEMBER, "(all)"): False,
        ("export-site", JANUARY, "(all)"): True, ("export-site", DECEMBER, "(all)"): False}


def test_only_places_listed_for_ga4_have_rows():
    unknown = {**place("unknown", "country"), "country_code": "unknown", "sources": []}
    other_country = {**place("sample-be", "country"), "country_code": "BE"}
    tables = ga4_tables(
        ref__current_web_properties=[ga4_property("export-site"), ga4_property("api-site")],
        stg__ga4_pulls=[ga4_pull("export-site", "export_engagement"),
                        ga4_pull("api-site", "engagement")],
        core__fct_ga4_export_v1=[
            export_fact("export-site"),
            export_fact("export-site", place_id="unknown", country="unknown")],
        core__fct_ga4_v1=[
            api_fact("api-site"),
            api_fact("api-site", place_id="sample-be", country="BE")],
    )
    tables["ref.current_locations"] = [*tables["ref.current_locations"], unknown,
                                       other_country]
    found = sorted({(r["web_property_id"], r["country_code"], r["geo_id"])
                    for r in engagement(tables)})
    assert found == [("api-site", "NL", "sample-nl"), ("export-site", "NL", "sample-nl")]

    every = rewritten("engagement_monthly_v1",
                      "  WHERE place_row.kind IN ('country', 'region', 'city')\n"
                      "    AND STRPOS",
                      "  WHERE place_row.kind = 'country'\n    OR STRPOS")
    assert sorted({(r["web_property_id"], r["geo_id"])
                   for r in engagement(tables, every)}) == [
        ("api-site", "sample-be"), ("api-site", "sample-nl"), ("export-site", "sample-nl"),
        ("export-site", "unknown")]


def test_an_export_row_the_newest_period_does_not_hold_is_left_out():
    older, newer = date(2026, 2, 2), date(2026, 2, 9)
    tables = ga4_tables(
        ref__current_web_properties=[ga4_property("sample-site")],
        stg__ga4_pulls=[ga4_pull("sample-site", "export_engagement")],
        core__fct_ga4_export_v1=[
            export_fact("sample-site", users=90, period_start=older, pulled_at=EARLIER),
            export_fact("sample-site", channel="Direct", users=40, period_start=older,
                        pulled_at=EARLIER),
            export_fact("sample-site", users=100, period_start=newer),
            export_fact("sample-site", channel="Organic Search", users=70,
                        period_start=newer)],
    )
    assert sorted((r["channel_group"], r["total_users"]) for r in engagement(tables)) == [
        ("(all)", 100), ("Organic Search", 70)]

    stale = rewritten("engagement_monthly_v1",
                      "    AND export_periods.period_start = export_row.period_start\n", "")
    assert sorted((r["channel_group"], r["total_users"])
                  for r in engagement(tables, stale)) == [
        ("(all)", 100), ("Direct", 40), ("Organic Search", 70)]


def page_fact(prop: str, path: str, month: date = JANUARY, users: int = 20,
              period_start: date = date(2026, 2, 2), pulled_at: datetime = THEN) -> dict:
    return {"month": month, "client_brand_id": "sample", "web_property_id": prop,
            "page_path": path, "through_date": date(2026, 1, 31),
            "period_start": period_start, "page_views": users * 3, "users": users,
            "engagement_seconds": users * 6, "pulled_at": pulled_at}


def named_page(path: str, group: str) -> dict:
    return {"load_number": 2, "page_path": path, "page_group": group}


def page_engagement(tables: dict[str, list[dict]], report: Report | None = None) -> list[dict]:
    report = report or {r.name: r for r in load()}["page_engagement_monthly_v1"]
    return run_statement(report, tables, today=TODAY)


def test_only_named_pages():
    tables = ga4_tables(
        ref__current_web_properties=[ga4_property("sample-site")],
        stg__ga4_pulls=[ga4_pull("sample-site", "export_pages")],
        core__fct_ga4_export_pages_v1=[page_fact("sample-site", "/pricing"),
                                       page_fact("sample-site", "/blog/a-post"),
                                       page_fact("sample-site", "/Pricing")],
        ref__current_pages=[named_page("/pricing", "Commercial")],
    )
    rows = page_engagement(tables)
    assert [(r["page_path"], r["page_group"], r["users"], r["ga4_route"]) for r in rows] == [
        ("/pricing", "Commercial", 20, "export")]
    assert rows[0]["avg_engagement_sec"] == 6.0

    every = rewritten("page_engagement_monthly_v1",
                      "JOIN ref.current_pages AS named_page\n",
                      "LEFT JOIN ref.current_pages AS named_page\n")
    assert sorted(r["page_path"] for r in page_engagement(tables, every)) == [
        "/Pricing", "/blog/a-post", "/pricing"]


def test_no_page_rows_on_the_data_api_route():
    tables = ga4_tables(
        ref__current_web_properties=[ga4_property("api-site"), ga4_property("export-site")],
        stg__ga4_pulls=[ga4_pull("api-site", "export_pages", EARLIER),
                        ga4_pull("api-site", "landing_pages"),
                        ga4_pull("export-site", "export_pages")],
        core__fct_ga4_export_pages_v1=[page_fact("api-site", "/pricing"),
                                       page_fact("export-site", "/pricing")],
        ref__current_pages=[named_page("/pricing", "Commercial")],
    )
    assert [r["web_property_id"] for r in page_engagement(tables)] == ["export-site"]

    every = rewritten("page_engagement_monthly_v1", "WHERE routes.ga4_route = 'export'\n  AND",
                      "WHERE")
    assert sorted(r["web_property_id"] for r in page_engagement(tables, every)) == [
        "api-site", "export-site"]


def test_a_page_the_newest_period_does_not_hold_is_left_out():
    older, newer = date(2026, 2, 2), date(2026, 2, 9)
    tables = ga4_tables(
        ref__current_web_properties=[ga4_property("sample-site")],
        stg__ga4_pulls=[ga4_pull("sample-site", "export_pages")],
        core__fct_ga4_export_pages_v1=[
            page_fact("sample-site", "/pricing", users=20, period_start=older,
                      pulled_at=EARLIER),
            page_fact("sample-site", "/about", users=5, period_start=older,
                      pulled_at=EARLIER),
            page_fact("sample-site", "/pricing", users=25, period_start=newer)],
        ref__current_pages=[named_page("/pricing", "Commercial"),
                            named_page("/about", "Company")],
    )
    assert [(r["page_path"], r["users"]) for r in page_engagement(tables)] == [
        ("/pricing", 25)]

    stale = rewritten("page_engagement_monthly_v1",
                      "  AND page_periods.period_start = page_row.period_start\n", "")
    assert sorted((r["page_path"], r["users"]) for r in page_engagement(tables, stale)) == [
        ("/about", 5), ("/pricing", 25)]


ACCOUNT = "sample-account"


def ads_row(campaign: int, impressions: int, cost_micros: int, *, level: str = "country",
            place: str = "sample-nl", country: str = "NL", location_type: str = "presence",
            channel: str = "SEARCH", clicks: int = 10, month: date = JANUARY,
            pulled_at: datetime = THEN) -> dict:
    return {"month": month, "client_brand_id": "sample", "ad_account_id": ACCOUNT,
            "campaign_id": campaign, "country_code": country, "geo_level": level,
            "location_id": place, "location_type": location_type, "currency_code": "EUR",
            "channel_type": channel, "impressions": impressions, "clicks": clicks,
            "cost_micros": cost_micros, "conversions": 1, "days_covered": 31,
            "pulled_at": pulled_at}


def bid(campaign: int, text: str, impressions: int, month: date = JANUARY) -> dict:
    return {"month": month, "client_brand_id": "sample", "ad_account_id": ACCOUNT,
            "campaign_id": campaign, "keyword_text": text, "match_type": "EXACT",
            "impressions": impressions, "pulled_at": THEN}


def auction_row(campaign: int, **counts: int) -> dict:
    given = {"impressions": 100, "auction_impressions": 100, "eligible_impressions": 400,
             "absolute_top_impressions": 20, "absolute_top_eligible_impressions": 50,
             "rank_lost_impressions": 100, "rank_lost_eligible_impressions": 400,
             "budget_lost_impressions": 200, "budget_lost_eligible_impressions": 400, **counts}
    return {"month": JANUARY, "client_brand_id": "sample", "ad_account_id": ACCOUNT,
            "campaign_id": campaign, "currency_code": "EUR", "channel_type": "SEARCH",
            **given, "impression_share_bounded": False, "pulled_at": THEN}


def ads_place(location: str, kind: str, country: str = "NL",
              sources: tuple[str, ...] = ("google-ads",)) -> dict:
    return {**place(location, kind), "country_code": country, "sources": list(sources)}


def ad_account(account: str = ACCOUNT, client_brand: str | None = "sample") -> dict:
    return {"load_number": 2, "ad_account_id": account, "client_brand_id": client_brand}


def pull(report: str, start: date = JANUARY, end: date = date(2026, 1, 31),
         account: str = ACCOUNT) -> dict:
    return {"client_brand_id": "sample", "ad_account_id": account, "report": report,
            "period": f"{start.isoformat()}-{report}", "window_start": start,
            "window_end": end, "pulled_at": THEN}


GEO_REPORTS = ("geography", "geography_regions", "geography_cities")


def paid_tables(**given: list[dict]) -> dict[str, list[dict]]:
    tables = {
        "core.fct_ads_v1": [],
        "core.fct_ads_keywords_v1": [],
        "core.fct_ads_auction_v1": [],
        "ref.current_locations": [ads_place("sample-nl", "country"),
                                  ads_place("sample-region", "region"),
                                  ads_place("sample-city", "city"),
                                  ads_place("sample-be", "country", "BE"),
                                  ads_place("sample-unlisted", "city", sources=("ga4",))],
        "ref.current_brands": [{**brand("sample", "client"), "aliases": ["Sample Co"]},
                               brand("rival", "competitor")],
        "ref.current_benchmark_keywords": [],
        "mart.search_demand_monthly_v1": [],
        "ref.current_ad_accounts": [ad_account()],
        "stg.google_ads_pulls": [pull(report) for report in GEO_REPORTS],
    }
    return {**tables, **{t.replace("__", "."): rows for t, rows in given.items()}}


def paid(tables: dict[str, list[dict]], report: Report | None = None) -> dict[tuple, dict]:
    report = report or {r.name: r for r in load()}["paid_monthly_v1"]
    rows = run_statement(report, tables, today=TODAY)
    return {(r["geo_id"], r["campaign_type"]): r for r in rows}


def test_delivery_reads_presence_alone():
    rows = [ads_row(1, 100, 5_000_000), ads_row(1, 400, 9_000_000, location_type="interest")]
    tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=[
        bid(1, "running shoes", 10)])
    found = paid(tables)
    (row,) = found.values()
    assert (row["impressions"], row["clicks"], row["cost"], row["avg_cpc"]) == (100, 10, 5, 0.5)
    assert (row["geo_level"], row["currency_code"]) == ("national", "EUR")

    every = rewritten("paid_monthly_v1", "  WHERE fact.location_type = 'presence'\n    AND ",
                      "  WHERE ")
    (added,) = paid(tables, every).values()
    assert (added["impressions"], added["cost"]) == (500, 14)


def test_a_search_campaigns_label_follows_its_keywords():
    rows = [ads_row(1, 100, 1_000_000), ads_row(2, 50, 1_000_000),
            ads_row(3, 1000, 1_000_000, channel="PERFORMANCE_MAX"),
            ads_row(4, 10, 1_000_000, channel="DEMAND_GEN"),
            ads_row(5, 10, 1_000_000, channel="SHOPPING")]
    bids = [bid(1, "sample shoes", 60), bid(1, "[sample-co] boots", 20),
            bid(1, "running shoes", 30), bid(2, "Sample", 10), bid(2, "samples", 30),
            bid(2, "running shoes", 40)]
    tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=bids)
    labels = {key[1]: row["impressions"] for key, row in paid(tables).items()}
    assert labels == {"Search (brand)": 100, "Search (category)": 50, "Performance Max": 1000,
                      "Demand Gen": 10, "SHOPPING": 10}

    flipped = rewritten("paid_monthly_v1",
                        "AND COALESCE(brand_totals.impressions, 0) * 2 > bid_totals.impressions",
                        "AND COALESCE(brand_totals.impressions, 0) * 2 < bid_totals.impressions")
    swapped = {key[1]: row["impressions"] for key, row in paid(tables, flipped).items()}
    assert (swapped["Search (brand)"], swapped["Search (category)"]) == (50, 100)


def test_a_search_campaign_with_no_keyword_impressions_is_unlabelled():
    rows = [ads_row(1, 100, 1_000_000), ads_row(2, 50, 1_000_000), ads_row(3, 7, 1_000_000)]
    bids = [bid(1, "running shoes", 30), bid(2, "running shoes", 0)]
    tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=bids,
                         core__fct_ads_auction_v1=[auction_row(3)])
    found = paid(tables)
    labels = {key[1]: row["impressions"] for key, row in found.items()}
    assert labels == {"Search (category)": 100, "Search (unlabelled)": 57}
    assert found[("sample-nl", "Search (unlabelled)")]["auction_impressions"] == 100

    before = rewritten("paid_monthly_v1",
                       "        AND (bid_totals.impressions IS NULL OR bid_totals.impressions"
                       " = 0)\n", "        AND FALSE\n")
    assert {key[1] for key in paid(tables, before)} == {"Search (category)"}


def test_a_search_campaigns_brand_keywords_count_as_brand():
    rows = [ads_row(1, 100, 1_000_000), ads_row(2, 50, 1_000_000), ads_row(3, 7, 1_000_000)]
    bids = [bid(1, "smpl trainers", 30), bid(1, "smpl trainers sale", 30),
            bid(1, "trainers", 50), bid(2, "rival boots", 40), bid(2, "trainers", 30),
            bid(3, "smpel trainers", 40), bid(3, "running shoes", 30)]
    belgian = [{**keyword("smpl trainers", "brand", "sample"), "country_code": "BE"},
               {**keyword("smpel trainers", "brand", None, canonical="smpl trainers"),
                "country_code": "BE"},
               keyword("rival boots", "competitor", "rival"),
               keyword("trainers", "generic", None)]
    tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=bids,
                         ref__current_benchmark_keywords=belgian)
    labels = {key[1]: row["impressions"] for key, row in paid(tables).items()}
    assert labels == {"Search (brand)": 107, "Search (category)": 50}


    none = {key[1]: row["impressions"] for key, row in paid(
        paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=bids)).items()}
    assert none == {"Search (category)": 157}

    names_only = rewritten("paid_monthly_v1", "WHERE own_keyword.term_type = 'brand'",
                           "WHERE own_keyword.term_type = 'none'")
    assert {key[1] for key in paid(tables, names_only)} == {"Search (category)"}


    own_only = rewritten("paid_monthly_v1",
                         "COALESCE(canonical_keyword.brand_id, own_keyword.brand_id)",
                         "own_keyword.brand_id")
    assert {key[1]: row["impressions"] for key, row in paid(tables, own_only).items()} == {
        "Search (brand)": 100, "Search (category)": 57}


def test_brand_impressions_above_brand_volume_are_flagged():
    def volume_row(place_: str, searches: int | None, brand_: str = "sample") -> dict:
        return {"month": JANUARY, "client_brand_id": "sample", "country_code": "NL",
                "geo_id": place_, "term_type": "brand", "brand": brand_,
                "search_volume": searches}

    rows = [ads_row(1, 100, 1_000_000),
            ads_row(1, 30, 1_000_000, level="city", place="sample-city"),
            ads_row(1, 20, 1_000_000, level="region", place="sample-region"),
            ads_row(2, 500, 1_000_000)]
    bids = [bid(1, "sample shoes", 10), bid(2, "running shoes", 10)]
    volumes = [volume_row("sample-nl", 90), volume_row("sample-nl", 1000, "rival"),
               volume_row("sample-city", 40)]
    tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=bids,
                         mart__search_demand_monthly_v1=volumes)
    found = paid(tables)
    assert found[("sample-nl", "Search (brand)")]["exceeds_brand_volume"] is True
    assert found[("sample-city", "Search (brand)")]["exceeds_brand_volume"] is False
    assert found[("sample-region", "Search (brand)")]["exceeds_brand_volume"] is None
    assert found[("sample-nl", "Search (category)")]["exceeds_brand_volume"] is None

    other_way = rewritten("paid_monthly_v1",
                          "delivered.impressions > brand_volume.search_volume",
                          "delivered.impressions < brand_volume.search_volume")
    flagged_ = paid(tables, other_way)
    assert (flagged_[("sample-nl", "Search (brand)")]["exceeds_brand_volume"],
            flagged_[("sample-city", "Search (brand)")]["exceeds_brand_volume"]) == (False, True)


def test_listed_places_from_their_reports():
    rows = [ads_row(1, 100, 1_000_000),
            ads_row(1, 40, 1_000_000, level="region", place="sample-region"),
            ads_row(1, 60, 1_000_000, level="region", place="sample-nl"),
            ads_row(1, 30, 1_000_000, level="city", place="sample-city"),
            ads_row(1, 70, 1_000_000, level="city", place="sample-nl"),
            ads_row(1, 5, 1_000_000, level="city", place="sample-unlisted"),
            ads_row(1, 9, 1_000_000, month=date(2023, 12, 1))]
    tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=[
        bid(1, "sample shoes", 10)])
    found = paid(tables)
    assert {key: (r["geo_level"], r["impressions"]) for key, r in found.items()} == {
        ("sample-nl", "Search (brand)"): ("national", 100),
        ("sample-region", "Search (brand)"): ("state", 40),
        ("sample-city", "Search (brand)"): ("city", 30)}

    loose = rewritten("paid_monthly_v1", "    AND places.kind = delivery.geo_level\n", "\n")
    assert paid(tables, loose)[("sample-nl", "Search (brand)")]["impressions"] == 230


def test_the_auction_counts_go_to_one_national_row():
    rows = [ads_row(1, 100, 1_000_000), ads_row(1, 300, 1_000_000, place="sample-be",
                                                country="BE"),
            ads_row(1, 30, 1_000_000, level="city", place="sample-city"),
            ads_row(2, 50, 1_000_000), ads_row(3, 10, 1_000_000, channel="PERFORMANCE_MAX")]
    bids = [bid(1, "sample shoes", 10), bid(2, "running shoes", 10)]
    auction = [auction_row(1), auction_row(2, auction_impressions=50, eligible_impressions=200),
               auction_row(3)]
    tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=bids,
                         core__fct_ads_auction_v1=auction)
    found = paid(tables)
    belgium = found[("sample-be", "Search (brand)")]
    assert (belgium["auction_impressions"], belgium["eligible_impressions"],
            belgium["impression_share"], belgium["absolute_top_impression_share"],
            belgium["rank_lost_impression_share"],
            belgium["budget_lost_impression_share"]) == (100, 400, 0.25, 0.4, 0.25, 0.5)
    assert found[("sample-nl", "Search (category)")]["impression_share"] == 0.25
    for key in [("sample-nl", "Search (brand)"), ("sample-city", "Search (brand)"),
                ("sample-nl", "Performance Max")]:
        assert found[key]["eligible_impressions"] is None, key
        assert found[key]["impression_share_bounded"] is None, key

    everywhere = rewritten("paid_monthly_v1",
                           "  AND auction.country_code = delivered.country_code\n", "\n")
    twice = paid(tables, everywhere)
    assert twice[("sample-nl", "Search (brand)")]["eligible_impressions"] == 400


def test_a_campaign_has_one_label_a_month():
    rows = [ads_row(1, 100, 1_000_000),
            ads_row(1, 30, 1_000_000, level="city", place="sample-city", channel=None,
                    pulled_at=EARLIER)]
    tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=[
        bid(1, "sample shoes", 10)])
    assert {key: r["impressions"] for key, r in paid(tables).items()} == {
        ("sample-nl", "Search (brand)"): 100, ("sample-city", "Search (brand)"): 30}

    split = rewritten(
        "paid_monthly_v1",
        "    MAX(ran.channel_type) AS channel_type\n  FROM delivery AS ran\n"
        "  GROUP BY ran.month, ran.client_brand_id, ran.ad_account_id, ran.campaign_id\n",
        "    ran.channel_type\n  FROM delivery AS ran\n"
        "  GROUP BY ran.month, ran.client_brand_id, ran.ad_account_id, ran.campaign_id,\n"
        "    ran.channel_type\n")
    assert {key: r["impressions"] for key, r in paid(tables, split).items()} == {
        ("sample-nl", "Search (brand)"): 100, ("sample-nl", "unknown"): 100,
        ("sample-city", "Search (brand)"): 30, ("sample-city", "unknown"): 30}


def test_a_month_is_complete_for_every_account():
    other = "sample-account-2"
    rows = [ads_row(1, 100, 1_000_000),
            ads_row(1, 30, 1_000_000, level="city", place="sample-city")]
    bids = [bid(1, "sample shoes", 10)]
    accounts = [ad_account(), ad_account(other), ad_account("sample-account-3", "sample-other")]
    short = [*(pull(r) for r in GEO_REPORTS), *(pull(r, account=other) for r in GEO_REPORTS[:2]),
             pull("geography_cities", JANUARY, date(2026, 1, 20), account=other)]

    def complete(pulls: list[dict], report: Report | None = None) -> dict[str, bool]:
        tables = paid_tables(core__fct_ads_v1=rows, core__fct_ads_keywords_v1=bids,
                             ref__current_ad_accounts=accounts, stg__google_ads_pulls=pulls)
        return {key[0]: r["is_complete_month"] for key, r in paid(tables, report).items()}

    assert complete(short) == {"sample-nl": True, "sample-city": False}
    whole = [*short, pull("geography_cities", date(2026, 1, 21), date(2026, 1, 31),
                          account=other)]
    assert complete(whole) == {"sample-nl": True, "sample-city": True}

    rows_alone = rewritten(
        "paid_monthly_v1",
        "    AND COALESCE(brand_coverage.days_covered, delivered.days_covered)\n"
        "      = DATE_DIFF(DATE_ADD(delivered.month, INTERVAL 1 MONTH), delivered.month, DAY)\n",
        "\n")
    assert complete(short, rows_alone) == {"sample-nl": True, "sample-city": True}


