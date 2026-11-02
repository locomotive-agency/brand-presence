"""The source BigQuery fake and DataForSEO fixture, through the real task workflow."""
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml
from google.cloud import bigquery

from brand_presence import runner, schema
from brand_presence.runs import Period, period_start
from brand_presence.settings import Settings
from brand_presence.sources.search_volume import PATH, SearchVolumeSource
from brand_presence.vendors.dataforseo import BASE
from brand_presence.warehouse import Client
from fakes import FakeBigQuery

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    config = yaml.safe_load((ROOT / "example.yaml").read_text())
    config["client_id"] = "aardvark-research"
    return config


class Killed(BaseException):
    pass


class BigQuery(FakeBigQuery):
    def create_dataset(self, dataset, exists_ok=False):
        identity = f"{dataset.project}.{dataset.dataset_id}"
        if identity not in self._datasets:
            self.add_dataset(identity, location=dataset.location)
        found = bigquery.Dataset(identity)
        found.location = self._datasets[identity][0]
        return found


class Vendor:
    def __init__(self, router):
        self.tasks = {}
        self.post_calls = 0
        self.get_calls = 0
        self.look_calls = 0
        self.kill_after_post = False
        self.timeout_after_post = False
        self.hide_ready = False
        self.waiting = False
        self.refuse = False
        path = ROOT / "tests/fixtures/dataforseo/search_volume.json"
        self.template = json.loads(path.read_text())
        router.post(f"{BASE}/{PATH}/task_post").mock(side_effect=self.post)
        router.get(f"{BASE}/{PATH}/tasks_ready").mock(side_effect=self.ready)
        router.get(url__regex=f"{BASE}/{PATH}/task_get/.*").mock(side_effect=self.get)

    def post(self, request):
        self.post_calls += 1
        replies = []
        for body in json.loads(request.content):
            identity = f"task-{len(self.tasks) + 1}"
            self.tasks[identity] = body
            replies.append({"id": identity, "status_code": 20100, "data": body})
        if self.kill_after_post:
            self.kill_after_post = False
            raise Killed()
        if self.timeout_after_post:
            self.timeout_after_post = False
            raise httpx.ReadTimeout("example")
        return httpx.Response(200, json={"status_code": 20000, "tasks": replies})

    def ready(self, request):
        self.look_calls += 1
        results = [] if self.hide_ready else [
            {"id": key, "tag": body["tag"]} for key, body in self.tasks.items()]
        return httpx.Response(200, json={"status_code": 20000,
                                        "tasks": [{"status_code": 20000, "result": results}]})

    def get(self, request):
        self.get_calls += 1
        identity = request.url.path.rsplit("/", 1)[-1]
        task = copy.deepcopy(self.template)
        task.update(id=identity, data=self.tasks[identity])
        if self.waiting:
            task.update(status_code=40602, result=None)
        if self.refuse:
            task.update(status_code=40501, result=None)
        return httpx.Response(200, json={"status_code": 20000, "tasks": [task]})


class Project:
    def __init__(self, tmp_path, router):
        self.now = datetime(2026, 3, 2, 12, tzinfo=UTC)
        self.settings = Settings.model_validate(configuration())
        self.settings.state_dir = tmp_path
        self.fake = BigQuery(project=self.settings.project_id, clock=lambda: self.now)
        self.bq = Client(self.fake, self.settings)
        self.vendor = Vendor(router)
        schema.initialise(self.bq)
        self.lines = []

    def later(self):
        self.now += timedelta(hours=2)

    def rows(self, table):
        return self.fake.rows(self.settings.table(table))

    def run(self, period="2026-02"):
        with httpx.Client() as client:
            world = runner.World(self.bq, client, {"dataforseo-login": "example-login",
                                                  "dataforseo-password": "example-password"},
                                 clock=lambda: self.now, sleep=lambda _: None,
                                 status_out=self.lines.append, wait_seconds=0)
            return runner.run(SearchVolumeSource(), self.settings, world,
                              period=Period(period, period_start(period)))
