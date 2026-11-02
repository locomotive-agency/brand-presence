from datetime import date, timedelta

import pytest
from google.api_core.exceptions import InternalServerError

from brand_presence.reports import runner
from brand_presence.reports.definition import load
from project import Project
from reporting import evaluate, install


def test_production_sql_selects_the_latest_pull_and_keeps_null(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    assert project.run().status == "success"
    rows = project.rows("stg.search_volume")
    older = next(r for r in rows if r["keyword"] == "sample aardvark theepot"
                 and r["month"] == date(2026, 1, 1))
    # Equal collection times are resolved by period. A new null must replace an old number.
    rows += [dict(older, search_volume=999, pulled_at=older["pulled_at"] - timedelta(days=1)),
             dict(older, search_volume=None, period="2026-03")]
    report = load()[0]
    result = evaluate(report.statement, {"stg.search_volume": (list(rows[0]), rows)})
    known = {(r["keyword"], r["month"]): r["search_volume"] for r in result}
    assert known == {
        ("sample aardvark theepot", "2025-12-01"): 410,
        ("sample aardvark theepot", "2026-01-01"): None,
        ("sample aardvark theepot", "2026-02-01"): 320,
        ("voorbeeld theepot kopen", "2025-12-01"): 2400,
        ("voorbeeld theepot kopen", "2026-01-01"): 1600,
        ("voorbeeld theepot kopen", "2026-02-01"): 1900,
    }


def test_full_report_build_is_idempotent_and_appends_only_changes(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    assert project.run().status == "success"
    install(project.fake)

    def build():
        return runner.run(project.bq, project.settings, clock=lambda: project.now,
                          out=project.lines.append)

    assert build() == ["core.fct_search_volume_v1", "mart.search_volume_history_v1"]
    assert len(project.rows("core.fct_search_volume_v1")) == 6
    assert len(project.rows("mart.search_volume_history_v1")) == 6
    before = project.rows("core.fct_search_volume_v1")
    project.later()
    build()
    assert project.rows("core.fct_search_volume_v1") == before
    old = project.rows("stg.search_volume")[0]
    project.fake.put(project.settings.table("stg.search_volume"),
                     [dict(old, period="2026-03", search_volume=17, pulled_at=project.now)])
    project.later()
    build()
    assert len(project.rows("core.fct_search_volume_v1")) == 7
    result = project.rows("mart.search_volume_history_v1")
    assert len(result) == 6
    assert next(r["search_volume"] for r in result if r["keyword"] == old["keyword"]
                and r["month"] == old["month"]) == 17


def test_failed_report_keeps_both_previous_tables(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.run()
    install(project.fake)
    runner.run(project.bq, project.settings, clock=lambda: project.now, out=lambda _: None)
    before = project.rows("mart.search_volume_history_v1")
    project.fake.fail("statement", match="INSERT INTO .*bp_mart", times=1)
    project.later()
    with pytest.raises(InternalServerError):
        runner.run(project.bq, project.settings, clock=lambda: project.now, out=lambda _: None)
    assert project.rows("mart.search_volume_history_v1") == before
    assert len(project.rows("core.fct_search_volume_v1")) == 6
    assert project.rows("ops.runs")[-1]["status"] == "error"


def test_history_report_keeps_early_months_and_latest_revision():
    report = load()[1]
    row = dict(month="2022-05-01", client_brand_id="example", country_code="GB",
               location_id="gb", keyword="example kettle", term_type="brand",
               subject_brand_id="example", benchmark_version=1, search_volume=None,
               pulled_at="2026-10-27T00:00:00+00:00")
    rows = [row, dict(row, search_volume=17, pulled_at="2026-10-26T00:00:00+00:00")]
    assert evaluate(report.statement, {"core.fct_search_volume_v1": (list(row), rows)}) == [row]
