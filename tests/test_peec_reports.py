"""Peec and Cloro remain independent even with colliding local answer references."""
from brand_presence.reports.definition import load
from report_samples import (
    TODAY,
    ai_answer,
    ai_brand,
    ai_tables,
    citation_tables,
    result_row,
)
from report_sql import run_statement


def result(brand, mentioned, **changes):
    return {**result_row("sample-run", "sample-prompt", brand, mentioned,
                       1 if mentioned else None), "answer_key": "sample-answer",
            "citation_known": True, **changes}


def test_peec_report_columns_mirror_cloro_without_changing_it():
    reports = {r.name: r for r in load()}
    for new, old in [("peec_prompt_results_v1", "ai_prompt_results_v2"),
                     ("peec_keyword_monthly_v1", "ai_keyword_monthly_v2"),
                     ("peec_citations_v1", "ai_citations_v1")]:
        new_columns = {c["name"] for c in reports[new].columns}
        assert {c["name"] for c in reports[old].columns} <= new_columns
        assert "source" not in new_columns
        assert "source" not in {c["name"] for c in reports[old].columns}
        assert not any("fct_ai_" in read for read in reports[new].definition["reads"])


def test_peec_keyword_counts_match_cloro_and_keep_distinct_chats():
    rows = [result("sample", False), result("rival", True)]
    reports = {r.name: r for r in load()}
    old = run_statement(reports["ai_keyword_monthly_v2"],
                        {"mart.ai_prompt_results_v2": rows}, today=TODAY)
    new = run_statement(reports["peec_keyword_monthly_v1"],
                        {"mart.peec_prompt_results_v1": rows}, today=TODAY)
    assert new == old
    doubled = rows + [{**r, "answer_key": "sample-second"} for r in rows]
    found = run_statement(reports["peec_keyword_monthly_v1"],
                          {"mart.peec_prompt_results_v1": doubled}, today=TODAY)
    assert all(r["prompts_run"] == 2 for r in found)
    assert next(r for r in found if r["brand"] == "sample")["gap_count"] == 2


def test_peec_unknown_client_citations_are_not_a_zero_count():
    rows = [result("sample", True, client_domain_cited=None, citation_known=False)]
    report = {r.name: r for r in load()}["peec_keyword_monthly_v1"]
    found = run_statement(report, {"mart.peec_prompt_results_v1": rows}, today=TODAY)
    assert found[0]["client_cited_count"] is None


def test_no_overview_facts_and_reports_match_cloro_null_marks_and_rates():
    from datetime import date

    from report_samples import THEN

    reports = {r.name: r for r in load()}
    answer = {**ai_answer(state="none shown"), "mapping": "sample-mapping",
        "answer_key": "sample-absent", "request_key": "sample-request",
        "observation_date": date(2026, 1, 31), "answer_text": "",
        "prompt_version": "sample-version", "citation_known": True}
    pull = {"request_key": answer["request_key"], "period": answer["period"],
            "pulled_at": THEN, "complete_snapshot": True}
    # An unrelated row gives the SQL stand-in the empty join's column names.
    unrelated = {**ai_brand("sample"), "mapping": answer["mapping"],
        "answer_key": "sample-other", "vendor_mentioned": True, "vendor_position": 1}
    clean = {"stg.peec_answers": [answer], "stg.peec_answer_brands": [unrelated],
             "stg.peec_pulls": [pull]}
    facts = run_statement(reports["fct_peec_answers_v1"], clean, today=TODAY)
    assert len(facts) == 1
    assert (facts[0]["subject_brand_id"], facts[0]["state"], facts[0]["named"],
            facts[0]["cited"]) == ("(none)", "none shown", None, None)
    assert run_statement(reports["fct_peec_cited_links_v1"], clean, today=TODAY) == []
    tables = ai_tables([ai_answer(state="none shown")],
                       [ai_brand("sample", prompt="other-prompt")])
    tables["ref.current_locations"][0]["sources"].append("peec")
    tables.update({"core.fct_peec_answers_v1": [{**r, "pulled_at": THEN} for r in facts],
        "core.fct_peec_cited_links_v1": [], "stg.peec_pulls": [pull],
        "stg.peec_prompts": [{"mapping": answer["mapping"], "prompt_id": answer["prompt_id"],
            "version": "sample-version", "prompt_text": "Invented prompt", "topic_id": None,
            "is_archived": False, "observed_at": THEN}]})
    old = run_statement(reports["ai_prompt_results_v2"], tables, today=TODAY)
    shown = run_statement(reports["peec_prompt_results_v1"], tables, today=TODAY)
    fields = ("brand", "answer_state", "brand_mentioned", "mention_rank", "client_domain_cited")
    assert {tuple(r[f] for f in fields) for r in shown} == {
        tuple(r[f] for f in fields) for r in old}
    assert {r["brand"] for r in shown} == {"sample", "rival", "other"}
    assert all(r["cited_urls"] == [] and r["answer_snippet"] is None for r in shown)
    for include_answered in (False, True):
        rows = shown + ([{**r, "answer_key": "sample-answered", "answer_state": "answered",
            "brand_mentioned": True, "mention_rank": 1, "client_domain_cited": False}
            for r in shown] if include_answered else [])
        monthly = run_statement(reports["peec_keyword_monthly_v1"],
            {"mart.peec_prompt_results_v1": rows}, today=TODAY)
        assert len(monthly) == 3
        assert all(r["client_cited_count"] == 0 for r in monthly)
        assert next(r for r in monthly if r["brand"] == "sample")["gap_count"] == 0
        assert all(r["prompts_no_text"] == 1 and r["prompts_run"] == int(include_answered)
                   and r["share_of_voice"] == (1 if include_answered else None) for r in monthly)
    tables = citation_tables(shown)
    tables["mart.peec_prompt_results_v1"] = tables.pop("mart.ai_prompt_results_v2")
    assert run_statement(reports["peec_citations_v1"], tables, today=TODAY) == []


def test_peec_citations_match_cloro_and_withhold_unknown_lists():
    rows = [result(brand, False, cited_urls=["https://sample.example/page"])
            for brand in ("sample", "rival")]
    reports = {r.name: r for r in load()}
    old = run_statement(reports["ai_citations_v1"], citation_tables(rows), today=TODAY)
    tables = citation_tables(rows)
    tables["mart.peec_prompt_results_v1"] = tables.pop("mart.ai_prompt_results_v2")
    new = run_statement(reports["peec_citations_v1"], tables, today=TODAY)
    assert new == old
    rows.extend([{**r, "answer_key": "sample-second"} for r in list(rows)])
    assert all(r["citations"] == 2 for r in run_statement(
        reports["peec_citations_v1"], tables, today=TODAY))
    for row in rows:
        row["citation_known"] = False
    assert run_statement(reports["peec_citations_v1"], tables, today=TODAY) == []


def test_peec_month_is_observation_month_and_empty_pull_removes_serving_rows():
    from datetime import date, timedelta

    from report_samples import THEN

    report_set = {r.name: r for r in load()}
    common = dict(mapping="sample-mapping", answer_key="sample-answer",
        request_key="sample-request",
        client_brand_id="sample", country_code="NL", location_id="sample-nl",
        prompt_id="sample-prompt", engine="sample-engine", period="2026-W05",
        period_start=date(2026, 1, 26), pulled_at=THEN, benchmark_version=2)
    answer = {**common, "observation_date": date(2026, 1, 31), "state": "answered",
        "answer_text": "Sample is named.", "prompt_version": "sample-version",
        "citation_known": True,
        "cited_links": ["https://sample.example/page"]}
    mark = {**common, "subject_brand_id": "sample", "named": True, "cited": True,
        "first_mention_at": 0, "mention_rank": 1, "vendor_mentioned": True,
        "vendor_position": 7}
    pull = {"request_key": common["request_key"], "period": common["period"],
            "pulled_at": THEN, "complete_snapshot": True}
    clean = {"stg.peec_answers": [answer], "stg.peec_answer_brands": [mark],
             "stg.peec_pulls": [pull]}
    fact = run_statement(report_set["fct_peec_answers_v1"], clean, today=TODAY)
    links = run_statement(report_set["fct_peec_cited_links_v1"], clean, today=TODAY)
    assert len(fact) == len(links) == 1
    assert fact[0]["month"] == date(2026, 1, 1)
    assert fact[0]["mention_rank"] == 1 and fact[0]["vendor_position"] == 7
    tables = ai_tables([ai_answer()], [ai_brand("sample")])
    tables["core.fct_ai_answers_v1"] = []
    tables["core.fct_ai_cited_links_v1"] = []
    tables["ref.current_locations"][0]["sources"].append("peec")
    tables.update({"core.fct_peec_answers_v1": [{**r, "pulled_at": THEN} for r in fact],
                   "core.fct_peec_cited_links_v1": [
                       {**r, "pulled_at": THEN + timedelta(hours=1)} for r in links],
                   "stg.peec_pulls": [pull]})
    tables["stg.peec_prompts"] = [dict(mapping=common["mapping"],
        prompt_id=common["prompt_id"], version="sample-version", prompt_text="Peec text",
        topic_id="sample-topic", is_archived=False, observed_at=THEN)]
    shown = run_statement(report_set["peec_prompt_results_v1"], tables, today=TODAY)
    assert len(shown) == 1 and "source" not in shown[0]
    assert shown[0]["prompt_text"] == "Peec text"
    assert shown[0]["topic_id"] == "sample-topic"
    assert shown[0]["keyword"] is None
    tables["stg.peec_prompts"].append({**tables["stg.peec_prompts"][0],
        "version": "sample-archived-version", "is_archived": True,
        "observed_at": THEN + timedelta(days=1)})
    archived = run_statement(report_set["peec_prompt_results_v1"], tables, today=TODAY)
    assert len(archived) == 1 and archived[0]["is_archived"]
    assert archived[0]["prompt_text"] == "Peec text"

    assert shown[0]["run_date"] == date(2026, 1, 31)
    assert shown[0]["pulled_at"] == THEN + timedelta(hours=1)
    assert shown[0]["cited_urls"] == ["https://sample.example/page"]
    assert shown[0]["sentiment"] is None and shown[0]["is_recommended"] is None
    # A new complete zero marker replaces the request even while appended old facts exist.
    tables["stg.peec_pulls"] = [{**pull, "pulled_at": THEN + timedelta(days=1)}]
    assert run_statement(report_set["peec_prompt_results_v1"], tables, today=TODAY) == []
    clean.update({"stg.peec_answers": [], "stg.peec_answer_brands": [],
                  "stg.peec_pulls": tables["stg.peec_pulls"]})
    assert run_statement(report_set["fct_peec_answers_v1"], clean, today=TODAY) == []
    assert run_statement(report_set["fct_peec_cited_links_v1"], clean, today=TODAY) == []
