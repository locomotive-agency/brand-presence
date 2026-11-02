from collections import Counter

import pytest

from brand_presence import runner
from brand_presence.bigquery import Warehouse
from project import Killed, Project


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
    project.now += timedelta(days=31)
    project.vendor.hide_ready = True
    project.run(period="2026-03")
    history = [body for body in project.vendor.tasks.values() if is_history(body)]
    assert len(history) == 1
    assert len(project.vendor.tasks) == 3
    assert project.vendor.look_calls == 1
    assert sum(row["state"] == "unknown" for row in project.rows("ops.search_volume_tasks")) == 2


def test_session_cleanup_failure_is_reported(tmp_path, respx_mock):
    from google.api_core.exceptions import InternalServerError

    from brand_presence.bigquery import Transaction

    project = Project(tmp_path, respx_mock)
    project.fake.fail("statement", match=r"CALL BQ.ABORT_SESSION", times=1)
    with pytest.raises(InternalServerError):
        with Transaction(project.bq):
            pass
