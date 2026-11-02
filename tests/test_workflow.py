from collections import Counter

import pytest

from brand_presence import runner
from brand_presence.bigquery import Warehouse
from project import Killed, Project


def test_old_snapshot_keeps_unchanged_benchmark_version(tmp_path, respx_mock):
    import json

    from brand_presence import client_ref

    project = Project(tmp_path, respx_mock)
    first = client_ref.apply(project.bq, project.settings, project.now)
    snapshot = project.rows('ops.config_snapshots')[0]
    payload = first.model_dump(mode='json', include={
        'load_number', 'snapshot_id', 'benchmark_sets', 'locations',
    })
    snapshot.update(snapshot_id='old-shape', payload=json.dumps(payload))
    project.fake.tables['bp_ops.config_snapshots'].rows = [snapshot]

    current = client_ref.apply(project.bq, project.settings, project.now)

    assert current.load_number == 2
    assert current.benchmark_sets[0].version == first.benchmark_sets[0].version == 1
    assert project.run().status == 'success'
    assert {row['benchmark_version'] for row in project.rows('stg.search_volume')} == {1}


def test_full_run_and_rerun(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    result = project.run()
    assert result.status == "success", result.lines
    assert len(project.vendor.tasks) == 2
    assert len(project.rows("raw.search_volume")) == 2
    assert len(project.rows("stg.search_volume")) == 6
    assert len(project.rows("ops.search_volume_history")) == 2
    assert len(list(tmp_path.rglob("*.json"))) == 2
    assert {r["state"] for r in project.rows("ops.search_volume_tasks")} == {"collected"}
    assert project.run().status == "success"
    assert len(project.vendor.tasks) == 2
    assert len(project.rows("raw.search_volume")) == 2
    assert len(project.rows("stg.search_volume")) == 6


def test_interrupted_post_is_looked_for_before_any_payment(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.vendor.kill_after_post = True
    with pytest.raises(Killed):
        project.run()
    assert {r["state"] for r in project.rows("ops.search_volume_tasks")} == {"sending"}
    project.later()
    assert project.run().status == "success"
    assert project.vendor.look_calls == 1
    assert project.vendor.post_calls == 1
    assert len(project.rows("stg.search_volume")) == 6
    assert [r["status"] for r in project.rows("ops.runs")] == ["error", "success"]
    assert project.rows("ops.runs")[0]["error_class"] == "did not finish"


def test_missing_ready_task_stays_unknown_without_repaying(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.vendor.timeout_after_post = True
    assert project.run().status == "error"
    project.vendor.hide_ready = True
    assert project.run().status == "error"
    assert {r["state"] for r in project.rows("ops.search_volume_tasks")} == {"unknown"}
    assert project.vendor.post_calls == 1
    project.vendor.hide_ready = False
    assert project.run().status == "success"
    assert project.vendor.post_calls == 1


@pytest.mark.parametrize("boundary", ["raw_file", "raw_load", "clean_write"])
def test_interruption_at_storage_boundaries_resumes_once(
        tmp_path, respx_mock, monkeypatch, boundary):
    project = Project(tmp_path, respx_mock)
    method = {"raw_file": "save_raw", "raw_load": "load_raw", "clean_write": "replace"}[boundary]
    original = getattr(Warehouse, method)

    def killed(*args, **kwargs):
        original(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(Warehouse, method, killed)
        with pytest.raises(Killed):
            project.run()
    project.later()
    assert project.run().status == "success"
    assert project.vendor.post_calls == 1
    assert len(project.rows("raw.search_volume")) == 2
    rows = project.rows("stg.search_volume")
    assert len(rows) == 6
    assert max(Counter((r["keyword"], r["month"], r["period"]) for r in rows).values()) == 1


def test_a_live_run_blocks_another_run_before_vendor_calls(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.vendor.kill_after_post = True
    with pytest.raises(Killed):
        project.run()
    assert project.run().status == "error"
    assert project.vendor.post_calls == 1
    assert project.vendor.look_calls == 0


def test_a_failed_clean_write_preserves_raw_and_recovers(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.fake.fail("statement", match=r"INSERT INTO .*bp_stg\.search_volume", times=1)
    assert project.run().status == "error"
    assert len(project.rows("raw.search_volume")) == 2
    assert project.rows("stg.search_volume") == []
    assert project.run().status == "success"
    assert project.vendor.post_calls == 1


def test_queued_tasks_are_collected_by_id_without_a_ready_lookup(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.vendor.waiting = True
    assert project.run().status == "error"
    assert {r["state"] for r in project.rows("ops.search_volume_tasks")} == {"submitted"}
    project.vendor.waiting = False
    assert project.run().status == "success"
    assert project.vendor.post_calls == 1
    assert project.vendor.look_calls == 0


def test_raw_is_not_written_clean_before_it_is_loaded(tmp_path, respx_mock, monkeypatch):
    project = Project(tmp_path, respx_mock)
    original = runner._Run.write

    def checked(self, answers):
        if answers:
            assert project.rows("raw.search_volume")
            assert list(tmp_path.rglob("*.json"))
        return original(self, answers)

    monkeypatch.setattr(runner._Run, "write", checked)
    assert project.run().status == "success"


def test_expired_paid_tasks_are_not_submitted_again(tmp_path, respx_mock):
    from datetime import timedelta

    project = Project(tmp_path, respx_mock)
    project.vendor.waiting = True
    project.run()
    project.now += timedelta(days=31)
    assert project.run().status == "error"
    assert {r["state"] for r in project.rows("ops.search_volume_tasks")} == {"expired"}
    assert project.vendor.post_calls == 1


def test_history_bookkeeping_failure_is_retried_without_payment(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.fake.fail("statement", match="INSERT INTO .*search_volume_history", times=1)
    assert project.run().status == "error"
    assert not project.rows("ops.search_volume_history")
    assert project.run().status == "success"
    assert project.vendor.post_calls == 1
    assert len(project.rows("ops.search_volume_history")) == 2


def test_recovered_task_uses_original_config_labels(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.vendor.waiting = True
    project.run()
    project.settings.benchmark_sets[0].keywords[0].term_type = "generic"
    project.settings.benchmark_sets[0].keywords[0].brand_id = None
    project.vendor.waiting = False
    assert project.run().status == "success"
    assert project.vendor.post_calls == 1
    rows = project.rows("stg.search_volume")
    assert {r["term_type"] for r in rows if r["keyword"] == "sample aardvark theepot"} == {"brand"}
    assert {r["benchmark_version"] for r in rows} == {1}


@pytest.mark.parametrize("code", [None, 50000, 20000])
def test_unclear_task_post_response_stays_unknown(tmp_path, respx_mock, code):
    import httpx

    from brand_presence.sources.search_volume import PATH
    from brand_presence.vendors.dataforseo import BASE

    project = Project(tmp_path, respx_mock)

    def unclear(request):
        response = project.vendor.post(request)
        body = response.json()
        for task in body["tasks"]:
            task["id"] = None
            task["status_code"] = code
        return httpx.Response(200, json=body)

    respx_mock.post(f"{BASE}/{PATH}/task_post").mock(side_effect=unclear)
    project.run()
    assert {r["state"] for r in project.rows("ops.search_volume_tasks")} == {"unknown"}
    project.vendor.hide_ready = True
    project.run()
    assert project.vendor.post_calls == 1


def test_new_month_does_not_repeat_unresolved_history(tmp_path, respx_mock):
    from datetime import timedelta

    from brand_presence.sources.search_volume import is_history

    project = Project(tmp_path, respx_mock)
    project.vendor.timeout_after_post = True
    project.run()
    project.now += timedelta(days=30)
    project.vendor.hide_ready = True
    project.run(period="2026-03")
    history = [body for body in project.vendor.tasks.values() if is_history(body)]
    assert len(history) == 1
    assert len(project.vendor.tasks) == 3
    assert project.vendor.look_calls == 1
    assert sum(row["state"] == "unknown" for row in project.rows("ops.search_volume_tasks")) == 2


@pytest.mark.parametrize("state", ["unknown", "expired", "sending", "submitted"])
@pytest.mark.parametrize("age,history_count", [(30, 0), (31, 1)])
def test_history_reservations_end_after_retention(
        tmp_path, respx_mock, monkeypatch, state, age, history_count):
    from datetime import timedelta

    from brand_presence import ledger
    from brand_presence.sources.search_volume import SearchVolumeTask, is_history

    project = Project(tmp_path, respx_mock)
    if state == "sending":
        original = ledger.Ledger.add

        def killed(self, rows):
            original(self, rows)
            raise Killed()

        with monkeypatch.context() as patch:
            patch.setattr(ledger.Ledger, "add", killed)
            with pytest.raises(Killed):
                project.run()
        assert project.vendor.post_calls == 0
    else:
        project.vendor.timeout_after_post = state == "unknown"
        project.vendor.waiting = True
        project.run()
        if state == "expired":
            book = ledger.Ledger(project.bq, SearchVolumeTask, lambda: project.now)
            book.rewrite([ledger.changed(row, state=ledger.EXPIRED)
                          for row in book.of_period("2026-02")])
    before = len(project.vendor.tasks)
    project.vendor.hide_ready = True
    project.vendor.waiting = False
    project.now += timedelta(days=age)
    project.run(period="2026-03")
    new_tasks = list(project.vendor.tasks.values())[before:]
    assert sum(is_history(body) for body in new_tasks) == history_count
    assert len(new_tasks) == 1 + history_count


def test_session_cleanup_failure_is_reported(tmp_path, respx_mock):
    from google.api_core.exceptions import InternalServerError

    from brand_presence.bigquery import Transaction

    project = Project(tmp_path, respx_mock)
    project.fake.fail("statement", match=r"CALL BQ.ABORT_SESSION", times=1)
    with pytest.raises(InternalServerError):
        with Transaction(project.bq):
            pass


def test_reserved_history_pairs_are_reported_once_per_run(tmp_path, respx_mock):
    from brand_presence.settings import Keyword

    project = Project(tmp_path, respx_mock)
    project.settings.benchmark_sets[0].keywords = [
        Keyword(keyword=f"term {n}", term_type="generic") for n in range(7)]
    project.vendor.timeout_after_post = True
    result = project.run()
    assert [line for line in result.lines if line.startswith("Reserved history pairs:")] == [
        "Reserved history pairs: 0"]
    project.vendor.hide_ready = True
    result = project.run(period="2026-03")
    lines = [line for line in result.lines if line.startswith("Reserved history pairs:")]
    assert lines == ["Reserved history pairs: 7; first 5: "
                     "('term 0', 'nl'), ('term 1', 'nl'), ('term 2', 'nl'), "
                     "('term 3', 'nl'), ('term 4', 'nl')"]


def test_recovered_history_is_not_reported_as_reserved(tmp_path, respx_mock):
    project = Project(tmp_path, respx_mock)
    project.vendor.timeout_after_post = True
    project.run()
    result = project.run(period="2026-03")
    assert [line for line in result.lines if line.startswith("Reserved history pairs:")] == [
        "Reserved history pairs: 0"]
    assert len(project.vendor.tasks) == 3


@pytest.mark.parametrize("reason", ["deadline", "nothing_taken"])
def test_unsent_requests_are_pending_in_run_counts(tmp_path, respx_mock, reason):
    from brand_presence.sources.search_volume import PATH
    from brand_presence.vendors.dataforseo import BASE

    project = Project(tmp_path, respx_mock)
    if reason == "deadline":
        project.fake.between("INSERT INTO .*config_snapshots", "SELECT .*search_volume_tasks",
                             project.later)
    else:
        respx_mock.post(f"{BASE}/{PATH}/task_post").respond(
            200, json={"status_code": 40202, "tasks": []})
    result = project.run()
    assert result.counts["requests_planned"] == 2
    assert result.counts["requests_sent"] == 0
    assert result.counts["requests_pending"] == 2
    assert result.counts["requests_failed"] == 0
    assert project.rows("ops.search_volume_tasks") == []
    saved = project.rows("ops.runs")[-1]
    assert saved["requests_pending"] == 2
    assert saved["requests_failed"] == 0


@pytest.mark.parametrize("check", ["counts", "attempts"])
def test_source_unavailable_requests_stay_pending_and_can_retry(
        tmp_path, respx_mock, monkeypatch, check):
    import json

    import httpx

    from brand_presence.sources.dataforseo_common import TASK_REFUSED
    from brand_presence.sources.search_volume import PATH, SearchVolumeSource
    from brand_presence.vendors.dataforseo import BASE

    project = Project(tmp_path, respx_mock)
    monkeypatch.setattr(SearchVolumeSource, "unavailable", (TASK_REFUSED,))

    def unavailable(request):
        return httpx.Response(200, json={"status_code": 20000, "tasks": [
            {"status_code": 40501, "data": body} for body in json.loads(request.content)]})

    route = respx_mock.post(f"{BASE}/{PATH}/task_post").mock(side_effect=unavailable)
    for _ in range(4):
        result = project.run()
    if check == "counts":
        assert result.counts["requests_pending"] == 2
        assert result.counts["requests_failed"] == 0
    else:
        assert route.call_count == 4
        route.mock(side_effect=project.vendor.post)
        assert project.run().status == "success"


def test_empty_raw_load_does_not_open_a_session(tmp_path, respx_mock):
    from brand_presence.sources.search_volume import SearchVolumeRaw

    project = Project(tmp_path, respx_mock)
    before = project.fake.statements()
    assert Warehouse(project.bq, tmp_path).load_raw(SearchVolumeRaw, []) == 0
    assert project.fake.statements() == before


def test_run_records_the_installed_package_version(tmp_path, respx_mock, monkeypatch):
    from importlib import metadata

    original = metadata.version
    monkeypatch.setattr(metadata, "version", lambda name: "9.8.7" if name == "brand-presence"
                        else original(name))
    project = Project(tmp_path, respx_mock)
    assert project.run().status == "success"
    assert project.rows("ops.runs")[-1]["image_version"] == "9.8.7"
