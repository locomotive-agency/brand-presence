import copy
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from brand_presence import runner
from brand_presence.bigquery import Warehouse
from brand_presence.runs import Period, period_start
from brand_presence.settings import Prompt, Settings
from brand_presence.sources.ai_answers import AiAnswersSource
from brand_presence.vendors.cloro import BASE
from project import Killed, Project, configuration


class Cloro:
    def __init__(self, router):
        self.tasks = {}
        self.calls = 0
        self.kill = False
        self.hide = False
        router.post(f"{BASE}/v1/async/task/batch").mock(side_effect=self.post)
        router.get(url__regex=f"{BASE}/v1/async/task/task-.*").mock(side_effect=self.get)

    def post(self, request):
        self.calls += 1
        results = []
        for body in json.loads(request.content):
            key = body["idempotencyKey"]
            if key not in self.tasks:
                self.tasks[key] = f"task-{len(self.tasks)}"
            results.append(
                {"task": {"id": self.tasks[key]}}
                if not self.hide
                else {"error": {"code": "RESOURCE_ALREADY_EXISTS"}}
            )
        if self.kill:
            self.kill = False
            raise Killed()
        return httpx.Response(200, json={"results": results})

    def get(self, request):
        answer = json.loads((Path(__file__).parent / "fixtures/cloro/answer.json").read_text())
        return httpx.Response(200, json=answer)


def prepared(tmp_path, router):
    project = Project(tmp_path, router)
    project.settings.brands[0].name = "Sample Brand Aardvark"
    project.settings.benchmark_sets[0].prompts = [
        Prompt(prompt_id="example-prompt", prompt="Which invented kettle?", engines=["chatgpt"])
    ]
    vendor = Cloro(router)
    return project, vendor


def run(project):
    with httpx.Client() as http:
        return runner.run(
            AiAnswersSource(),
            project.settings,
            runner.World(
                project.bq,
                http,
                {"cloro-api-key": "example-key"},
                clock=lambda: project.now,
                sleep=lambda _: None,
                wait_seconds=0,
            ),
            period=Period("2026-W10", period_start("2026-W10")),
        )


def test_full_cloro_run(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    assert run(project).status == "success"
    assert run(project).status == "success"
    assert vendor.calls == len(vendor.tasks) == 1
    assert len(project.rows("stg.ai_answers")) == 1
    assert project.rows("stg.ai_answer_brands")[0]["named"] is True
    assert project.rows("ref.benchmark_prompts")[0]["prompt_id"] == "example-prompt"
    assert len(list(tmp_path.rglob("*.json"))) == 1


@pytest.mark.parametrize("boundary", ["post", "save_raw", "load_raw", "replace"])
def test_cloro_interruption(tmp_path, respx_mock, monkeypatch, boundary):
    project, vendor = prepared(tmp_path, respx_mock)
    with monkeypatch.context() as patch:
        if boundary == "post":
            vendor.kill = True
        else:
            original = getattr(Warehouse, boundary)

            def killed(*args, **kwargs):
                original(*args, **kwargs)
                raise Killed()

            patch.setattr(Warehouse, boundary, killed)
        with pytest.raises(Killed):
            run(project)
    project.later()
    assert run(project).status == "success"
    assert len(vendor.tasks) == len(project.rows("stg.ai_answers")) == 1
    assert len(project.rows("raw.ai_answers")) == 1


def test_unknown_cloro_keeps_original_key(tmp_path, respx_mock):
    project, vendor = prepared(tmp_path, respx_mock)
    vendor.hide = True
    assert run(project).status == "error"
    assert run(project).status == "error"
    assert len(vendor.tasks) == 1
    assert project.rows("ops.ai_answers_tasks")[0]["state"] == "unknown"
    vendor.hide = False
    assert run(project).status == "success"
    assert len(vendor.tasks) == 1


@pytest.mark.parametrize('code,error_class', [
    ('INVALID', 'task refused'), ('MODEL_UNAVAILABLE', 'engine unavailable'),
])
def test_unknown_cloro_lookup_refusal_is_recorded_without_repeated_posts(
        tmp_path, respx_mock, code, error_class):
    from brand_presence import ledger
    from brand_presence.sources.ai_answers import AiAnswersTask

    project, vendor = prepared(tmp_path, respx_mock)
    vendor.hide = True
    assert run(project).status == 'error'
    route = respx_mock.post(f'{BASE}/v1/async/task/batch').mock(return_value=httpx.Response(
        200, json={'results': [{'success': False, 'error': {'code': code}}]},
    ))

    looked = run(project)

    row = project.rows('ops.ai_answers_tasks')[0]
    assert row['state'] == 'unknown'
    assert row['error_class'] == error_class
    assert row['attempt'] == 1
    assert looked.error_class == error_class
    calls = route.call_count
    assert run(project).status == 'error'
    assert route.call_count == calls
    assert len(project.rows('ops.ai_answers_tasks')) == 1

    # After the outcome is investigated, clearing the class permits the same key's lookup.
    held = ledger.Ledger(project.bq, AiAnswersTask, lambda: project.now)
    held.rewrite([ledger.changed(held.in_state(ledger.UNKNOWN)[0], error_class=None)])
    vendor.hide = False
    route.mock(side_effect=vendor.post)
    assert run(project).status == 'success'
    assert len(vendor.tasks) == 1
    assert len(project.rows('stg.ai_answers')) == 1


@pytest.mark.parametrize("change", ["engine", "location", "competitor", "labels"])
def test_cloro_config_refusals(change):
    config = copy.deepcopy(configuration())
    group = config["benchmark_sets"][0]
    group["prompts"] = [dict(prompt_id="example", prompt="Example?", engines=["chatgpt"])]
    if change == "engine":
        group["prompts"][0]["engines"] = ["invalid"]
    elif change == "location":
        group["prompts"][0]["location_ids"] = ["missing"]
    elif change == "competitor":
        group["competitors"] = ["missing"]
    else:
        group["prompts"][0]["keyword"] = "incomplete"
    with pytest.raises(ValidationError):
        Settings.model_validate(config)


def test_own_brand_edits_and_restored_sets_get_new_versions(tmp_path, respx_mock):
    from brand_presence import client_ref

    project, _ = prepared(tmp_path, respx_mock)
    first = client_ref.apply(project.bq, project.settings, project.now)
    project.settings.brands[0].domains = ['invented.example']
    second = client_ref.apply(project.bq, project.settings, project.now)
    assert first.benchmark_sets[0].version == 1
    assert second.benchmark_sets[0].version == 2
    group = project.settings.benchmark_sets.pop()
    client_ref.apply(project.bq, project.settings, project.now)
    project.settings.benchmark_sets.append(group)
    restored = client_ref.apply(project.bq, project.settings, project.now)
    assert restored.benchmark_sets[0].version == 3
    assert len(project.rows('ref.benchmark_prompts')) == 3


def test_unknown_old_cloro_week_is_not_submitted(tmp_path, respx_mock):
    from datetime import timedelta

    project, vendor = prepared(tmp_path, respx_mock)
    vendor.hide = True
    run(project)
    project.now += timedelta(days=7)
    run(project)
    assert vendor.calls == 1
    assert project.rows('ops.ai_answers_tasks')[0]['state'] == 'unknown'


def test_historical_run_cannot_submit_current_unknown_task(tmp_path, respx_mock, monkeypatch):
    from brand_presence.sources.ai_answers import CloroSend

    project, vendor = prepared(tmp_path, respx_mock)

    def killed(*args, **kwargs):
        raise Killed()

    with monkeypatch.context() as patch:
        patch.setattr(CloroSend, 'submit', killed)
        with pytest.raises(Killed):
            run(project)
    assert vendor.calls == 0
    project.later()
    with httpx.Client() as client:
        runner.run(AiAnswersSource(), project.settings, runner.World(
            project.bq, client, {'cloro-api-key': 'example-key'},
            clock=lambda: project.now, sleep=lambda _: None, wait_seconds=0),
            period=Period('2026-W09', period_start('2026-W09')))
    assert vendor.calls == 0
    assert project.rows('ops.ai_answers_tasks')[0]['state'] == 'unknown'
    assert run(project).status == 'success'
    assert len(vendor.tasks) == 1
